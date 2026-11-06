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
    expected_tables = {
        "user",
        "app",
        "chat_history",
        "model_call_log",
        "app_memory",
        "alembic_version",
    }
    assert expected_tables <= tables
    with sqlite3.connect(database_path) as connection:
        model_log_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(model_call_log)")
        }
        app_columns = {row[1] for row in connection.execute("PRAGMA table_info(app)")}
    assert {
        "call_kind",
        "error_summary",
        "retry_count",
        "cached_input_tokens",
        "cost_cny",
        "userId",
    } <= model_log_columns
    assert "deploy_revision" in app_columns
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



def test_user_id_is_backfilled_for_existing_model_calls(tmp_path: Path) -> None:
    database_path = tmp_path / "backfill.db"
    config = _config_for(database_path)
    command.upgrade(config, "0009_model_call_cache_cost")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO app (id, userId, priority, isDelete) VALUES (1, 42, 0, 0)"
        )
        connection.execute(
            """
            INSERT INTO model_call_log (
                id, appId, turn_id, model_name, call_kind, status, duration_ms,
                input_tokens, cached_input_tokens, output_tokens, cost_cny,
                retry_count
            ) VALUES (1, 1, 'legacy-turn', 'deepseek-flash', 'generation', 'success', 1,
                      100, 0, 10, 0, 0)
            """
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        user_id = connection.execute(
            "SELECT userId FROM model_call_log WHERE turn_id = 'legacy-turn'"
        ).fetchone()[0]
    assert user_id == 42
