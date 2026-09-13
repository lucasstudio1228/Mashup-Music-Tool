"""
migrations.py – Tự động phát hiện và xử lý schema thay đổi.
Chạy tại startup, trước create_db_and_tables().
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"


def _get_conn():
    return sqlite3.connect(str(DB_PATH))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate_remove_project_api_fields():
    """
    Migration M001: Xóa api_key, api_base_url, api_model khỏi bảng project.
    Dùng pattern: create_temp → copy → drop_old → rename.
    """
    if not DB_PATH.exists():
        return  # DB chưa tạo, không cần migrate

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "api_key"):
            return  # Đã migrate rồi

        print("  [Migration M001] Removing API fields from project table...")
        conn.execute("BEGIN")

        # Lấy schema hiện tại, lọc bỏ 3 cột API
        cur = conn.execute("PRAGMA table_info(project)")
        all_cols = [(row[1], row[2]) for row in cur.fetchall()]
        keep_cols = [
            (name, typ) for name, typ in all_cols
            if name not in ("api_key", "api_base_url", "api_model")
        ]
        col_names = [name for name, _ in keep_cols]
        col_defs = ", ".join(f"{name} {typ}" for name, typ in keep_cols)
        col_list = ", ".join(col_names)

        conn.execute(f"CREATE TABLE project_new ({col_defs})")
        conn.execute(
            f"INSERT INTO project_new ({col_list}) SELECT {col_list} FROM project")
        conn.execute("DROP TABLE project")
        conn.execute("ALTER TABLE project_new RENAME TO project")

        conn.execute("COMMIT")
        print("  [Migration M001] Done.")
    except Exception as e:
        conn.execute("ROLLBACK")
        raise RuntimeError(f"Migration M001 failed: {e}") from e
    finally:
        conn.close()


def migrate_create_stem_directories():
    """M002: Tạo thư mục stems/."""
    stems_root = Path(__file__).parent.parent / "stems"
    stems_root.mkdir(exist_ok=True)


def migrate_add_denoise_columns():
    """
    M003: Thêm cột den_other/den_bass/den_drums/den_vocals vào trackstem
    (độ mạnh khử noise thủ công mỗi stem, 0.0 = auto theo volume như cũ).
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "trackstem"):
            return  # Bảng chưa có → create_db_and_tables sẽ tạo đủ cột
        cols = ["den_other", "den_bass", "den_drums", "den_vocals"]
        missing = [c for c in cols if not _column_exists(conn, "trackstem", c)]
        if not missing:
            return  # Đã migrate rồi
        print(f"  [Migration M003] Adding denoise columns: {missing}")
        for col in missing:
            conn.execute(
                f"ALTER TABLE trackstem ADD COLUMN {col} FLOAT DEFAULT 0.0")
        conn.commit()
        print("  [Migration M003] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M003 failed: {e}") from e
    finally:
        conn.close()


def run_all():
    """Gọi hàm này ở startup, trước create_db_and_tables()."""
    migrate_remove_project_api_fields()   # M001
    migrate_create_stem_directories()      # M002
    migrate_add_denoise_columns()          # M003 — mới
    # Thêm migration mới vào đây theo thứ tự
