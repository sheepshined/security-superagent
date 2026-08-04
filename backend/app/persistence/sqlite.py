import sqlite3
from pathlib import Path

from app.core.config import PROJECT_ROOT


def resolve_sqlite_url(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("only sqlite:/// database URLs are supported in local mode")
    raw_path = database_url.removeprefix(prefix)
    if raw_path == ":memory:":
        return raw_path
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def create_sqlite_connection(database_url: str) -> sqlite3.Connection:
    connection = sqlite3.connect(
        resolve_sqlite_url(database_url),
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection
