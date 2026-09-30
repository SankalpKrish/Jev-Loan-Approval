from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Path of a not-yet-created SQLite file for an AuditLog (or any store) under test."""
    return tmp_path / "db" / "jevloan.db"
