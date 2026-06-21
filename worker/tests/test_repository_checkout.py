from types import SimpleNamespace

import pytest

from app import repository_checkout
from app.repository_checkout import (
    CloneError,
    CommitCheckoutError,
    RepositoryCheckoutError,
    RepositoryNotFoundError,
    build_clone_url,
    checkout_repository,
)


def _ok(stdout="", stderr=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr="error", returncode=1):
    return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


@pytest.fixture
def fake_fs(monkeypatch):
    """Stub tempfile.mkdtemp and shutil.rmtree so no real FS work happens."""
    monkeypatch.setattr(
        repository_checkout.tempfile, "mkdtemp", lambda **kw: "/tmp/work-1"
    )
    calls = {"rmtree": []}

    def fake_rmtree(path, onerror=None):
        calls["rmtree"].append(path)

    monkeypatch.setattr(repository_checkout.shutil, "rmtree", fake_rmtree)
    return calls


def _stub_git(monkeypatch, results):
    """results: callable(args) -> CompletedProcess-like."""
    monkeypatch.setattr(repository_checkout, "_run_git", results)


# --- build_clone_url ---


def test_build_clone_url_without_token():
    assert build_clone_url("octocat/hello") == "https://github.com/octocat/hello.git"


def test_build_clone_url_strips_git_suffix():
    assert (
        build_clone_url("octocat/hello.git")
        == "https://github.com/octocat/hello.git"
    )


def test_build_clone_url_with_token():
    url = build_clone_url("octocat/hello", token="secret")
    assert url == "https://x-access-token:secret@github.com/octocat/hello.git"


# --- happy path ---


def test_checkout_success_yields_path_and_cleans_up(fake_fs, monkeypatch):
    _stub_git(monkeypatch, lambda args, **kw: _ok())

    with checkout_repository("octocat/hello", "sha1") as checkout:
        assert checkout.path == "/tmp/work-1"
        assert checkout.repository == "octocat/hello"
        assert checkout.commit_sha == "sha1"

    # Workspace removed on exit.
    assert fake_fs["rmtree"] == ["/tmp/work-1"]


def test_checkout_runs_clone_then_checkout(fake_fs, monkeypatch):
    seen = []

    def runner(args, **kw):
        seen.append(args[0])
        return _ok()

    _stub_git(monkeypatch, runner)
    with checkout_repository("octocat/hello", "sha1"):
        pass

    assert "clone" in seen
    assert "checkout" in seen


# --- failure handling ---


def test_clone_failure_raises_clone_error(fake_fs, monkeypatch):
    def runner(args, **kw):
        if args[0] == "clone":
            return _fail("fatal: some clone problem")
        return _ok()

    _stub_git(monkeypatch, runner)
    with pytest.raises(CloneError):
        with checkout_repository("octocat/hello", "sha1"):
            pass
    # Cleanup still happens even when clone fails.
    assert fake_fs["rmtree"] == ["/tmp/work-1"]


def test_missing_repository_raises_not_found(fake_fs, monkeypatch):
    def runner(args, **kw):
        if args[0] == "clone":
            return _fail("remote: Repository not found.")
        return _ok()

    _stub_git(monkeypatch, runner)
    with pytest.raises(RepositoryNotFoundError):
        with checkout_repository("octocat/missing", "sha1"):
            pass


def test_checkout_failure_raises_commit_error(fake_fs, monkeypatch):
    def runner(args, **kw):
        if args[0] == "checkout":
            return _fail("error: pathspec 'sha1' did not match")
        return _ok()

    _stub_git(monkeypatch, runner)
    with pytest.raises(CommitCheckoutError):
        with checkout_repository("octocat/hello", "sha1"):
            pass
    assert fake_fs["rmtree"] == ["/tmp/work-1"]


def test_fetch_failure_is_best_effort(fake_fs, monkeypatch):
    def runner(args, **kw):
        if args[0] == "fetch":
            return _fail("fetch by sha not allowed")
        return _ok()

    _stub_git(monkeypatch, runner)
    # Fetch failing must NOT abort checkout.
    with checkout_repository("octocat/hello", "sha1") as checkout:
        assert checkout.path == "/tmp/work-1"


def test_cleanup_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        repository_checkout.tempfile, "mkdtemp", lambda **kw: "/tmp/work-2"
    )

    def exploding_rmtree(path, onerror=None):
        raise OSError("cannot remove")

    monkeypatch.setattr(repository_checkout.shutil, "rmtree", exploding_rmtree)
    _stub_git(monkeypatch, lambda args, **kw: _ok())

    # Cleanup failure is swallowed; the context exits normally.
    with checkout_repository("octocat/hello", "sha1") as checkout:
        assert checkout.path == "/tmp/work-2"


# --- argument validation ---


def test_missing_commit_sha_raises(fake_fs, monkeypatch):
    _stub_git(monkeypatch, lambda args, **kw: _ok())
    with pytest.raises(RepositoryCheckoutError):
        with checkout_repository("octocat/hello", ""):
            pass


def test_missing_repository_raises(monkeypatch):
    monkeypatch.setattr(
        repository_checkout.tempfile, "mkdtemp", lambda **kw: "/tmp/work-3"
    )
    with pytest.raises(RepositoryCheckoutError):
        with checkout_repository("", "sha1"):
            pass


# --- git executable / timeout mapping (via _run_git) ---


def test_run_git_missing_binary(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(repository_checkout.subprocess, "run", boom)
    with pytest.raises(RepositoryCheckoutError):
        repository_checkout._run_git(["clone"])
