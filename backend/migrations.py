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


def migrate_add_project_video_columns():
    """
    M004: Thêm cột video_idea + auto_video vào bảng project.
    - video_idea TEXT DEFAULT ''  : ý tưởng lưu sẵn để AI viết prompt.
    - auto_video BOOLEAN DEFAULT 0 : project CŨ mặc định TẮT auto-render.
      (Project MỚI tạo qua ORM nhận default True từ model, ghi rõ giá trị
       lúc INSERT nên không phụ thuộc DEFAULT của cột.)
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        added = []
        if not _column_exists(conn, "project", "video_idea"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN video_idea TEXT DEFAULT ''")
            added.append("video_idea")
        if not _column_exists(conn, "project", "auto_video"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN auto_video BOOLEAN DEFAULT 0")
            added.append("auto_video")
        if not added:
            return  # Đã migrate rồi
        print(f"  [Migration M004] Adding project video columns: {added}")
        conn.commit()
        print("  [Migration M004] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M004 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_video_style():
    """
    M005: Thêm cột video_style vào bảng project.
    - video_style TEXT DEFAULT '2d' : phong cách ảnh/video ("2d"/"3d").
      Cả project CŨ lẫn MỚI mặc định 2D theo yêu cầu.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "video_style"):
            return  # Đã migrate rồi
        print("  [Migration M005] Adding project column: video_style")
        conn.execute(
            "ALTER TABLE project ADD COLUMN video_style TEXT DEFAULT '2d'")
        conn.commit()
        print("  [Migration M005] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M005 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_youtube_upload_columns():
    """
    M006: Chuẩn bị cho tính năng auto-upload YouTube qua GPMLogin.
    - project: thêm instrument / music_style / auto_upload (project cũ: auto TẮT).
    - appsettings: thêm gpm_api_base_url / gpm_exe_path (cấu hình Local API).
    Hai bảng mới (channelmapping, youtubeupload) do create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        added = []
        if _table_exists(conn, "project"):
            if not _column_exists(conn, "project", "instrument"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN instrument TEXT DEFAULT ''")
                added.append("project.instrument")
            if not _column_exists(conn, "project", "music_style"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN music_style TEXT DEFAULT ''")
                added.append("project.music_style")
            if not _column_exists(conn, "project", "auto_upload"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN auto_upload BOOLEAN DEFAULT 0")
                added.append("project.auto_upload")
        if _table_exists(conn, "appsettings"):
            if not _column_exists(conn, "appsettings", "gpm_api_base_url"):
                conn.execute(
                    "ALTER TABLE appsettings ADD COLUMN gpm_api_base_url "
                    "TEXT DEFAULT 'http://localhost:9495'")
                added.append("appsettings.gpm_api_base_url")
            if not _column_exists(conn, "appsettings", "gpm_exe_path"):
                conn.execute(
                    "ALTER TABLE appsettings ADD COLUMN gpm_exe_path TEXT")
                added.append("appsettings.gpm_exe_path")
        if not added:
            return  # Đã migrate rồi
        print(f"  [Migration M006] Adding YouTube upload columns: {added}")
        conn.commit()
        print("  [Migration M006] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M006 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_suno_columns():
    """
    M007: Chuẩn bị STEP 0 (tạo nhạc nền Suno).
    - project: thêm music_source (mặc định 'local' → giữ nguyên import thủ công;
      'suno' = tạo 15 bài WAV từ Suno). Project cũ mặc định 'local'.
    Hai bảng mới (sunobatch, sunocandidate) do create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "music_source"):
            return  # Đã migrate rồi
        print("  [Migration M007] Adding project column: music_source")
        conn.execute(
            "ALTER TABLE project ADD COLUMN music_source TEXT DEFAULT 'local'")
        conn.commit()
        print("  [Migration M007] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M007 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_suno_idea_column():
    """
    M008: thêm project.suno_idea (ý tưởng/nhạc cụ cho STEP 0 — AI viết Styles từ
    đây, luôn nhạc không lời). Project cũ mặc định '' (rỗng).
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "suno_idea"):
            return  # Đã migrate rồi
        print("  [Migration M008] Adding project column: suno_idea")
        conn.execute(
            "ALTER TABLE project ADD COLUMN suno_idea TEXT DEFAULT ''")
        conn.commit()
        print("  [Migration M008] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M008 failed: {e}") from e
    finally:
        conn.close()


def run_all():
    """Gọi hàm này ở startup, trước create_db_and_tables()."""
    migrate_remove_project_api_fields()   # M001
    migrate_create_stem_directories()      # M002
    migrate_add_denoise_columns()          # M003
    migrate_add_project_video_columns()    # M004
    migrate_add_project_video_style()      # M005
    migrate_add_youtube_upload_columns()   # M006
    migrate_add_suno_columns()             # M007
    migrate_add_suno_idea_column()         # M008 — mới
    # Thêm migration mới vào đây theo thứ tự
