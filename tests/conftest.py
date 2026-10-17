"""Test isolation (15.8.4 cleanup): every test gets a throw-away tmp_path that is deleted afterwards (so mock blind keys
never persist under /tmp/pytest-of-*), and the private key store is redirected into it."""
import shutil, tempfile
import pytest

@pytest.fixture
def tmp_path():
    import pathlib
    d = tempfile.mkdtemp(prefix="llmbias-test-")
    try: yield pathlib.Path(d)
    finally: shutil.rmtree(d, ignore_errors=True)

@pytest.fixture(autouse=True)
def _private_store(tmp_path, monkeypatch):
    import llmbias.labels as LB
    monkeypatch.setenv("LLMBIAS_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setattr(LB, "PRIVATE", str(tmp_path / "private"))
    yield
