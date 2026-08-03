"""
Pytest configuration and shared fixtures for arXiv CS.CL Paper Matcher.
Ensures tests run isolated using temporary SQLite database files.
"""

import os
import pytest
import tempfile
from pathlib import Path

# Override DB path for testing before importing core_engine or server
@pytest.fixture(autouse=True)
def temp_db_env(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_paper_matcher.db"
    monkeypatch.setenv("PAPER_MATCHER_DB_PATH", str(test_db_path))
    monkeypatch.setenv("PAPER_MATCHER_DB_BUCKET", "")
    monkeypatch.setenv("AWS_S3_BUCKET", "")
    
    # Reload core engine DB_PATH
    import core_engine as core
    monkeypatch.setattr(core, "DB_PATH", test_db_path)
    monkeypatch.setattr(core, "DB_GCS_BUCKET", "")
    monkeypatch.setattr(core, "AWS_S3_BUCKET", "")
    
    core.init_db()
    yield test_db_path
    
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass
