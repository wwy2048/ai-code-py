import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def _config_for(database_path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_baseline_creates_schema_on_empty_database(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"

    command.upgrade(_config_for(database_path), "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        history_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_history)")}
    assert {"user", "app", "chat_history", "model_call_log", "alembic_version"} <= tables
    with sqlite3.connect(database_path) as connection:
        model_log_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(model_call_log)")
        }
    assert {"call_kind", "error_summary", "retry_count"} <= model_log_columns
    assert {
        "turn_id",
        "sequence",
        "message_kind",
        "payload",
        "visible",
        "active",
        "tool_call_id",
        "tool_name",
        "is_error",
        "compacted_by",
    } <= history_columns


def test_baseline_preserves_existing_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, sentinel TEXT)")
        connection.execute("CREATE TABLE app (id INTEGER PRIMARY KEY, sentinel TEXT)")
        connection.execute("CREATE TABLE chat_history (id INTEGER PRIMARY KEY, sentinel TEXT)")

    command.upgrade(_config_for(database_path), "head")

    with sqlite3.connect(database_path) as connection:
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(user)")}
        history_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_history)")}
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "sentinel" in user_columns
    assert "turn_id" in history_columns
    assert {"user", "app", "chat_history", "alembic_version"} <= tables
