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


def migrate_add_project_batch_id():
    """
    M009: thêm project.batch_id (nhóm project cùng 1 lô sản xuất hàng loạt).
    Project cũ mặc định NULL (tạo lẻ). Bảng mới `batchrun` do
    create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "batch_id"):
            return  # Đã migrate rồi
        print("  [Migration M009] Adding project column: batch_id")
        conn.execute("ALTER TABLE project ADD COLUMN batch_id TEXT")
        conn.commit()
        print("  [Migration M009] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M009 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_mix_settings():
    """
    M010: thêm project.mix_duration_minutes + mix_crossfade_seconds — thời lượng
    mix mà chuỗi tự động (Suno→Mix→Video) dùng; trước đây cứng 60'/15s.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        for col, default in (("mix_duration_minutes", 120.0),
                             ("mix_crossfade_seconds", 5.0)):
            if _column_exists(conn, "project", col):
                continue
            print(f"  [Migration M010] Adding project column: {col}")
            conn.execute(
                f"ALTER TABLE project ADD COLUMN {col} FLOAT DEFAULT {default}")
        conn.commit()
    except Exception as e:
        raise RuntimeError(f"Migration M010 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_trash_columns():
    """
    M011: thêm project.deleted_at + trash_dir (Thùng rác 7 ngày trước khi xoá
    vĩnh viễn).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        for col, ddl in (("deleted_at", "DATETIME"), ("trash_dir", "VARCHAR")):
            if not _column_exists(conn, "project", col):
                print(f"  [Migration M011] Adding project column: {col}")
                conn.execute(f"ALTER TABLE project ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M011 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_channel_visual_look():
    """
    M012: thêm channelmapping.visual_look (phong cách kênh — mỗi kênh 1 look
    riêng: phong cách ảnh + kiểu thumbnail + màu/chuyển cảnh video). Rỗng = tự gán
    ở lần đọc đầu (video/channel_looks.ensure_channel_looks).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "channelmapping"):
            return
        if not _column_exists(conn, "channelmapping", "visual_look"):
            print("  [Migration M012] Adding channelmapping column: visual_look")
            conn.execute("ALTER TABLE channelmapping ADD COLUMN visual_look "
                         "VARCHAR NOT NULL DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M012 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_thumbnail_ai_settings():
    """
    M013: cấu hình Thumbnail AI qua API (key/base URL/model riêng + bật/tắt +
    kiểm tra chữ) trong appsettings.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "appsettings"):
            return
        cols = {
            "thumb_api_key": "VARCHAR",
            "thumb_api_base_url": "VARCHAR NOT NULL DEFAULT 'https://api.shopaikey.com'",
            "thumb_api_model": "VARCHAR NOT NULL DEFAULT 'gemini-3-pro-image-preview'",
            "thumb_ai_enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "thumb_ai_verify": "BOOLEAN NOT NULL DEFAULT 1",
        }
        for col, ddl in cols.items():
            if not _column_exists(conn, "appsettings", col):
                print(f"  [Migration M013] Adding appsettings column: {col}")
                conn.execute(f"ALTER TABLE appsettings ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M013 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_thumbnail_engine():
    """
    M014: máy vẽ thumbnail mặc định (chatgpt | api | pil) trong appsettings —
    mặc định ChatGPT web từ 2026-10-23.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "appsettings"):
            return
        if not _column_exists(conn, "appsettings", "thumb_engine"):
            print("  [Migration M014] Adding appsettings column: thumb_engine")
            conn.execute("ALTER TABLE appsettings ADD COLUMN thumb_engine "
                         "VARCHAR NOT NULL DEFAULT 'chatgpt'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M014 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_channel_id():
    """
    M015: thêm project.channel_id — kênh YouTube người dùng chọn cho project,
    độc lập với nhạc cụ/thể loại. NULL = tra theo (instrument, music_style) như cũ.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "channel_id"):
            print("  [Migration M015] Adding project column: channel_id")
            conn.execute("ALTER TABLE project ADD COLUMN channel_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_project_channel_id "
                         "ON project (channel_id)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M015 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_intro_cast():
    """
    M016: thêm project.intro_mode ('thumbnail' = intro dựng từ ảnh thumbnail —
    MẶC ĐỊNH cả project cũ; 'font' = chữ font đè clip 00 như trước) và
    project.cast_mode ('character_pet' mặc định; 'scenery' = chỉ cảnh vật).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "intro_mode"):
            print("  [Migration M016] Adding project column: intro_mode")
            conn.execute("ALTER TABLE project ADD COLUMN intro_mode TEXT "
                         "DEFAULT 'thumbnail'")
        if not _column_exists(conn, "project", "cast_mode"):
            print("  [Migration M016] Adding project column: cast_mode")
            conn.execute("ALTER TABLE project ADD COLUMN cast_mode TEXT "
                         "DEFAULT 'character_pet'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M016 failed: {e}") from e
    finally:
        conn.close()


def migrate_create_custom_look_table():
    """
    M017: bảng customlook — phong cách kênh người dùng tự nhập (tên + mô tả →
    câu thẩm mỹ tiếng Anh, kế thừa 1 look có sẵn). Kể từ đây 2 kênh được dùng
    chung 1 phong cách (không còn ràng buộc mỗi kênh 1 look khác nhau).
    """
    if not DB_PATH.exists():
        return
    from backend.video.channel_looks import CUSTOM_TABLE_SQL
    conn = _get_conn()
    try:
        if not _table_exists(conn, "customlook"):
            print("  [Migration M017] Creating table: customlook")
            conn.execute(CUSTOM_TABLE_SQL)
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M017 failed: {e}") from e
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
    migrate_add_suno_idea_column()         # M008
    migrate_add_project_batch_id()         # M009
    migrate_add_project_mix_settings()     # M010
    migrate_add_project_trash_columns()    # M011 — Thùng rác
    migrate_add_channel_visual_look()      # M012 — phong cách kênh
    migrate_add_thumbnail_ai_settings()    # M013 — thumbnail AI qua API
    migrate_add_thumbnail_engine()         # M014 — thumbnail ChatGPT mặc định
    migrate_add_project_channel_id()       # M015 — kênh chọn riêng từng project
    migrate_add_project_intro_cast()       # M016 — intro thumbnail + chỉ cảnh vật
    migrate_create_custom_look_table()     # M017 — phong cách kênh tự nhập
    # Thêm migration mới vào đây theo thứ tự
