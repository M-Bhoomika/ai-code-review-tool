from unittest.mock import MagicMock

from app.diff_processor import (
    DiffChunk,
    DiffLine,
    ProcessedFile,
    calculate_diff_positions,
    chunk_large_diff,
    estimate_tokens,
    fetch_pr_files,
    parse_unified_diff,
    should_skip_file,
)

SIMPLE_DIFF = """@@ -1,3 +1,4 @@
 context1
-removed
+added1
+added2
 context2"""

MULTI_HUNK_DIFF = """@@ -1,2 +1,2 @@
 a
-b
+B
@@ -10,2 +10,3 @@
 c
+d
 e"""

ADDITIONS_ONLY_DIFF = """@@ -0,0 +1,2 @@
+line1
+line2"""


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10


def test_should_skip_file():
    assert should_skip_file("poetry.lock")
    assert should_skip_file("package.json")
    assert should_skip_file("README.md")
    assert should_skip_file("logo.PNG")  # case-insensitive
    assert not should_skip_file("app/main.py")


def test_empty_diff():
    assert parse_unified_diff("") == []
    assert parse_unified_diff(None) == []
    assert chunk_large_diff("file.py", "") == []


def test_simple_diff_parsing_and_positions():
    lines = parse_unified_diff(SIMPLE_DIFF)

    assert [(line.diff_position, line.line_type) for line in lines] == [
        (1, "context"),
        (2, "deletion"),
        (3, "addition"),
        (4, "addition"),
        (5, "context"),
    ]

    # New-file line numbers across the hunk.
    context1, removed, added1, added2, context2 = lines
    assert context1.new_line_number == 1 and context1.old_line_number == 1
    assert removed.old_line_number == 2 and removed.new_line_number is None
    assert added1.new_line_number == 2 and added1.old_line_number is None
    assert added2.new_line_number == 3
    assert context2.new_line_number == 4 and context2.old_line_number == 3
    assert added1.content == "added1"


def test_additions_only_diff():
    lines = parse_unified_diff(ADDITIONS_ONLY_DIFF)

    assert all(line.line_type == "addition" for line in lines)
    assert [line.new_line_number for line in lines] == [1, 2]
    assert [line.old_line_number for line in lines] == [None, None]
    assert [line.diff_position for line in lines] == [1, 2]


def test_additions_and_deletions_diff():
    lines = parse_unified_diff(SIMPLE_DIFF)
    additions = [line for line in lines if line.line_type == "addition"]
    deletions = [line for line in lines if line.line_type == "deletion"]

    assert len(additions) == 2
    assert len(deletions) == 1


def test_multiple_hunks_position_counting():
    lines = parse_unified_diff(MULTI_HUNK_DIFF)

    # The second hunk header consumes a position, so the line after it
    # ("c") lands at position 5 rather than 4.
    by_content = {line.content: line.diff_position for line in lines}
    assert by_content["a"] == 1
    assert by_content["b"] == 2
    assert by_content["B"] == 3
    assert by_content["c"] == 5
    assert by_content["d"] == 6
    assert by_content["e"] == 7

    # Line numbers reset according to the second hunk header (-10 +10).
    c_line = next(line for line in lines if line.content == "c")
    assert c_line.old_line_number == 10 and c_line.new_line_number == 10


def test_diff_position_correctness_mapping():
    lines = parse_unified_diff(SIMPLE_DIFF)
    positions = calculate_diff_positions(lines)

    # new_line_number -> diff_position, deletions excluded.
    assert positions == {1: 1, 2: 3, 3: 4, 4: 5}


def test_chunk_splitting_on_hunk_boundaries():
    # A tiny max_tokens forces each hunk into its own chunk.
    chunks = chunk_large_diff("file.py", MULTI_HUNK_DIFF, max_tokens=1)

    assert len(chunks) == 2
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]

    # No chunk splits a hunk: first hunk lines vs second hunk lines stay intact.
    first_contents = [line.content for line in chunks[0].diff_lines]
    second_contents = [line.content for line in chunks[1].diff_lines]
    assert first_contents == ["a", "b", "B"]
    assert second_contents == ["c", "d", "e"]


def test_chunk_packs_small_hunks_together():
    # A large budget keeps everything in a single chunk.
    chunks = chunk_large_diff("file.py", MULTI_HUNK_DIFF, max_tokens=10_000)

    assert len(chunks) == 1
    assert len(chunks[0].diff_lines) == 6
    assert chunks[0].token_estimate >= 0


def test_oversized_single_hunk_is_not_split():
    big_hunk = "@@ -1,1 +1,2 @@\n a\n" + "\n".join(
        f"+{'x' * 50}" for _ in range(20)
    )
    chunks = chunk_large_diff("file.py", big_hunk, max_tokens=1)

    # Cannot split inside a hunk, so it remains a single chunk.
    assert len(chunks) == 1


def test_fetch_pr_files_filters_and_processes():
    def make_file(filename, additions, deletions, status, patch):
        gh_file = MagicMock()
        gh_file.filename = filename
        gh_file.additions = additions
        gh_file.deletions = deletions
        gh_file.status = status
        gh_file.patch = patch
        return gh_file

    files = [
        make_file("app/main.py", 3, 1, "modified", SIMPLE_DIFF),
        make_file("package.json", 5, 0, "modified", "@@ -1 +1 @@\n+{}"),
        make_file("assets/logo.png", 0, 0, "added", None),  # binary
        make_file("generated.py", 2000, 0, "added", "@@ -0,0 +1 @@\n+x"),
        make_file("bin.dat", 10, 0, "added", None),  # patchless/binary
    ]

    pull_request = MagicMock()
    pull_request.get_files.return_value = files
    repo = MagicMock()
    repo.get_pull.return_value = pull_request
    client = MagicMock()
    client.get_repo.return_value = repo

    processed = fetch_pr_files(client, "octocat/hello-world", 7)

    assert len(processed) == 1
    result = processed[0]
    assert isinstance(result, ProcessedFile)
    assert result.file_path == "app/main.py"
    assert result.additions == 3
    assert result.deletions == 1
    assert result.status == "modified"
    assert len(result.chunks) == 1
    assert isinstance(result.chunks[0], DiffChunk)
    assert isinstance(result.chunks[0].diff_lines[0], DiffLine)

    client.get_repo.assert_called_once_with("octocat/hello-world")
    repo.get_pull.assert_called_once_with(7)
