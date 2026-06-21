from pathlib import Path

from app.repository_indexer import (
    CHUNK_OVERLAP_LINES,
    CHUNK_TARGET_LINES,
    CodeChunk,
    IndexingResult,
    chunk_source_file,
    detect_language,
    index_repository,
    should_index_file,
)


def test_detect_language():
    assert detect_language("app/main.py") == "python"
    assert detect_language("index.js") == "javascript"
    assert detect_language("component.jsx") == "javascript"
    assert detect_language("module.ts") == "typescript"
    assert detect_language("widget.tsx") == "typescript"
    assert detect_language("Main.java") == "java"
    assert detect_language("server.go") == "go"
    assert detect_language("lib.rs") == "rust"
    assert detect_language("engine.cpp") == "cpp"
    assert detect_language("util.c") == "c"
    assert detect_language("Program.cs") == "csharp"
    assert detect_language("notes.md") == "unknown"
    assert detect_language("Makefile") == "unknown"


def test_should_index_file_skips_directories():
    assert not should_index_file("node_modules/pkg/index.js")
    assert not should_index_file("project/dist/bundle.js")
    assert not should_index_file("build/output.go")
    assert not should_index_file("coverage/report.py")
    assert not should_index_file(".venv/lib/thing.py")
    assert not should_index_file("pkg/__pycache__/mod.py")


def test_should_index_file_accepts_normal_file(tmp_path):
    source = tmp_path / "main.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    assert should_index_file(str(source))


def test_should_index_file_skips_binary(tmp_path):
    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\x00\x01\x02binarycontent")
    assert not should_index_file(str(binary))


def test_should_index_file_skips_large_file(tmp_path):
    big = tmp_path / "big.py"
    big.write_text("a" * (1024 * 1024 + 10), encoding="utf-8")
    assert not should_index_file(str(big))


def test_chunk_generation_single_chunk():
    content = "\n".join(f"line{i}" for i in range(1, 51))  # 50 lines
    chunks = chunk_source_file(content, repository="repo", file_path="a.py")

    assert len(chunks) == 1
    assert isinstance(chunks[0], CodeChunk)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 50
    assert chunks[0].repository == "repo"
    assert chunks[0].file_path == "a.py"


def test_chunk_generation_and_overlap_logic():
    total_lines = 450
    content = "\n".join(f"line{i}" for i in range(1, total_lines + 1))
    chunks = chunk_source_file(content, repository="repo", file_path="big.py")

    step = CHUNK_TARGET_LINES - CHUNK_OVERLAP_LINES  # 180
    assert step == 180
    assert len(chunks) == 3

    assert (chunks[0].start_line, chunks[0].end_line) == (1, 200)
    assert (chunks[1].start_line, chunks[1].end_line) == (181, 380)
    assert (chunks[2].start_line, chunks[2].end_line) == (361, 450)

    # Overlap between consecutive chunks is exactly CHUNK_OVERLAP_LINES.
    overlap = chunks[0].end_line - chunks[1].start_line + 1
    assert overlap == CHUNK_OVERLAP_LINES

    # Line numbers are preserved: first line of chunk 1 is "line181".
    assert chunks[1].content.splitlines()[0] == "line181"


def test_empty_content_produces_no_chunks():
    assert chunk_source_file("") == []


def test_index_repository_result_counts(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8"
    )
    (tmp_path / "src" / "b.ts").write_text(
        "\n".join(f"line{i}" for i in range(1, 251)), encoding="utf-8"
    )
    # Skipped: excluded directory.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text(
        "console.log(1)\n", encoding="utf-8"
    )
    # Skipped: unknown language.
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    # Skipped: binary file.
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")

    result = index_repository(str(tmp_path), "octocat/hello", persist=False)

    assert isinstance(result, IndexingResult)
    assert result.repository == "octocat/hello"
    # a.py -> 1 chunk, b.ts (250 lines) -> 2 chunks.
    assert result.files_processed == 2
    assert result.chunks_created == 3


def test_index_repository_persists_when_enabled(tmp_path, monkeypatch):
    calls = {}

    def fake_store(chunks):
        calls["count"] = len(chunks)
        return len(chunks)

    import app.repository_indexer as indexer

    monkeypatch.setattr(indexer.vector_store, "store_chunks", fake_store)

    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    result = index_repository(str(tmp_path), "repo", persist=True)

    assert result.chunks_created == 1
    assert calls["count"] == 1
