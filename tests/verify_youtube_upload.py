"""tests/verify_youtube_upload.py — Kiểm tra 2 yêu cầu của bước đăng nháp YouTube:

  1. CÓ file thumbnail  → phải BẤM chọn hình thu nhỏ và nạp đúng file đó.
  2. Sau khi upload     → phải CHỜ YouTube tải lên xong VÀ quét bản quyền/chính
                          sách xong mới làm tác vụ tiếp theo (đóng dialog).

Chạy hoàn toàn bằng trang giả (fake page) — KHÔNG mở trình duyệt, KHÔNG đụng
YouTube, KHÔNG tốn credit.

    .venv/Scripts/python.exe tests/verify_youtube_upload.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.video import config, youtube_driver as yd  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


# ── Trang giả ────────────────────────────────────────────────────────────
class _Loc:
    def __init__(self, text: str | None):
        self._text = text

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._text is not None else 0

    def inner_text(self, timeout=None):
        if self._text is None:
            raise RuntimeError("not found")
        return self._text


class FakePage:
    """Trả text trạng thái theo kịch bản: mỗi lần đọc lấy 1 mốc trong `script`."""

    def __init__(self, script: list[str]):
        self.script = list(script)
        self.waits = 0
        self.reads = 0

    def locator(self, css: str):
        # Chỉ selector đầu tiên của upload_progress có text (giống Studio thật:
        # 1 khung trạng thái duy nhất) → tránh nhân đôi text.
        if css != config.YOUTUBE_SELECTORS["upload_progress"][0]:
            return _Loc(None)
        self.reads += 1
        idx = min(self.reads - 1, len(self.script) - 1)
        return _Loc(self.script[idx])

    def wait_for_timeout(self, ms: int):
        self.waits += 1


def _p(msg, pct):  # progress_cb giả
    pass


# ── 1) Chờ upload + quét chính sách ──────────────────────────────────────
def test_wait_upload_complete():
    print("\n[1] Chờ upload xong + quét bản quyền/chính sách xong")

    # Kịch bản thật: tải lên → xử lý → đang kiểm tra → xong, không vấn đề.
    script = (["Uploading 12%... 5 minutes left"] * 3
              + ["Uploading 87%... 1 minute left"] * 2
              + ["Upload complete. Processing will begin shortly."] * 2
              + ["Processing HD version, checking for issues..."] * 3
              + ["Checks complete. No issues found."] * 6)
    page = FakePage(script)
    warns: list[str] = []
    last = yd._wait_upload_complete(page, config.get_selectors("youtube"),
                                    _p, warns, timeout_s=60)
    check("Chờ qua hết pha uploading/processing/checking",
          page.reads >= len(script) - 6, f"đọc {page.reads} lần")
    check("Chỉ kết thúc khi Studio báo xong", "checks complete" in last, last)
    check("Không cảnh báo thừa khi mọi thứ sạch", warns == [], str(warns))

    # Trạng thái 'đang tải lên' tiếng Việt cũng phải được coi là CHƯA xong.
    page = FakePage(["Đang tải lên 40%...", "Đang tải lên 90%...",
                     "Đang kiểm tra...", "Đã kiểm tra xong. Không phát hiện vấn đề."])
    warns = []
    yd._wait_upload_complete(page, config.get_selectors("youtube"), _p, warns,
                             timeout_s=60)
    check("Nhận diện trạng thái tiếng Việt", page.reads >= 4, f"{page.reads} lần đọc")

    # Pha 'processing' kéo dài (video 2-3 tiếng): quét chính sách đã xong →
    # KHÔNG được chờ transcode thêm hàng giờ.
    page = FakePage(["Uploading 99%... 21 seconds left",
                     "Checking 18%... 9 minutes left",
                     "Video processing is taking longer than expected. Please wait."])
    warns = []
    last = yd._wait_upload_complete(page, config.get_selectors("youtube"), _p,
                                    warns, timeout_s=60)
    check("Quét chính sách xong + còn transcode → đi tiếp, không chờ hàng giờ",
          "processing" in last and warns == [], f"{warns} | {last}")

    # Chưa từng quét mà treo ở 'processing' → chờ có hạn rồi đi tiếp + cảnh báo.
    page = FakePage(["Uploading 99%... 21 seconds left",
                     "Upload complete ... processing will begin shortly",
                     "Video processing is taking longer than expected. Please wait."])
    warns = []
    yd._wait_upload_complete(page, config.get_selectors("youtube"), _p, warns,
                             timeout_s=60, processing_grace_s=0.1)
    check("Treo ở transcode mà chưa có kết quả quét → cảnh báo rõ",
          any("quét bản quyền" in w for w in warns), str(warns))

    # Cảnh báo (không raise) khi hết giờ mà vẫn đang tải.
    page = FakePage(["Uploading 3%... 4 hours left"])
    warns = []
    yd._wait_upload_complete(page, config.get_selectors("youtube"), _p, warns,
                             timeout_s=0.1)
    check("Hết giờ → cảnh báo chứ không raise", len(warns) == 1, str(warns))

    # Phát hiện vấn đề bản quyền → cảnh báo rõ.
    page = FakePage(["Uploading 50%..."] * 2
                    + ["Checks complete. Copyright issues found."] * 6)
    warns = []
    yd._wait_upload_complete(page, config.get_selectors("youtube"), _p, warns,
                            timeout_s=60)
    check("Báo vi phạm bản quyền → có cảnh báo",
          any("bản quyền" in w for w in warns), str(warns))

    check("'No issues found' KHÔNG bị hiểu nhầm là có vấn đề",
          not yd._has_issue("checks complete. no issues found."))
    check("'Copyright issues found' bị bắt đúng",
          yd._has_issue("copyright issues found"))


# ── 2) Bấm chọn hình thu nhỏ ─────────────────────────────────────────────
def test_thumbnail():
    print("\n[2] Bấm chọn hình thu nhỏ & nạp đúng file")
    sel = config.get_selectors("youtube")

    check("Có selector input thumbnail riêng (accept ảnh)",
          bool(sel.get("thumbnail_input")))
    check("Selector thumbnail KHÔNG trùng input video",
          not (set(sel["thumbnail_input"]) & set(sel["file_input"])),
          f"video={sel['file_input']}")
    check("Có selector nút 'Upload file / Tải tệp lên' (tiếng Anh lẫn Việt)",
          any("Upload file" in s for s in sel["thumbnail_button"])
          and any("Tải tệp lên" in s for s in sel["thumbnail_button"]))

    # Cách 1: input ảnh có sẵn → upload_first thành công + thấy ảnh xem trước.
    got: dict = {}
    orig_upload, orig_query = yd._bb.upload_first, yd._bb.query_first
    try:
        yd._bb.upload_first = lambda page, selectors, fp, timeout_ms=8000: (
            got.update({"selectors": selectors, "file": fp}) or True)
        yd._bb.query_first = lambda page, selectors, timeout_ms=8000: object()
        warns: list[str] = []
        ok = yd._set_thumbnail(FakePage([]), sel, r"D:\x\thumbnail.png", _p, warns)
        check("Nạp được thumbnail qua input ảnh", ok)
        check("Gửi ĐÚNG file thumbnail", got.get("file", "").endswith("thumbnail.png"),
              got.get("file", ""))
        check("Dùng đúng nhóm selector thumbnail",
              got.get("selectors") == sel["thumbnail_input"])
        check("Không cảnh báo khi thành công", warns == [], str(warns))
    finally:
        yd._bb.upload_first, yd._bb.query_first = orig_upload, orig_query

    # Cách 2: không có input sẵn & không bấm được nút → cảnh báo, KHÔNG raise.
    class _NoChooserPage(FakePage):
        def expect_file_chooser(self, timeout=None):
            raise RuntimeError("no file chooser")

    try:
        yd._bb.upload_first = lambda *a, **k: False
        yd._bb.query_first = lambda *a, **k: None
        warns = []
        ok = yd._set_thumbnail(_NoChooserPage([]), sel, r"D:\x\thumbnail.png",
                               _p, warns)
        check("Thumbnail lỗi → trả False + cảnh báo, không raise",
              ok is False and len(warns) == 1, str(warns))
    finally:
        yd._bb.upload_first, yd._bb.query_first = orig_upload, orig_query


# ── 3) Nối dây: service truyền thumbnail, driver nhận đúng tham số ───────
def test_wiring():
    print("\n[3] Nối dây service → driver")
    import inspect
    sig = inspect.signature(yd.upload_draft)
    check("upload_draft có tham số thumbnail_path", "thumbnail_path" in sig.parameters)

    src = Path(__file__).resolve().parents[1] / "backend/video/service.py"
    body = src.read_text(encoding="utf-8")
    check("step_upload_youtube truyền thumbnail sang driver",
          "thumbnail_path=str(thumb) if thumb.exists() else None" in body)

    drv = (Path(__file__).resolve().parents[1]
           / "backend/video/youtube_driver.py").read_text(encoding="utf-8")
    check("Đã bỏ chờ cứng 4 giây, thay bằng chờ theo trạng thái thật",
          "_wait_upload_complete(page, sel" in drv
          and "page.wait_for_timeout(4000)" not in drv)
    check("Chờ upload nằm TRƯỚC bước đóng dialog (lưu nháp)",
          drv.index("_wait_upload_complete(page, sel") < drv.index('sel["close_dialog"]'))
    sel = config.get_selectors("youtube")
    # Selector đọc được từ DOM Studio thật (2026-09-24):
    #   nút X dialog = ytcp-button#ytcp-uploads-dialog-close-button
    #   ngôn ngữ     = ytcp-form-language-input#language-input
    check("Có id thật của nút Đóng dialog upload",
          any("ytcp-uploads-dialog-close-button" in s for s in sel["close_dialog"]))
    check("Có selector thật của dropdown ngôn ngữ",
          any("language-input" in s for s in sel["video_language_dropdown"]))
    check("Vẫn TUYỆT ĐỐI không bấm Publish",
          "publish" not in drv.lower().replace("publish/xuất bản", "")
          or "Không bao giờ bấm Next→Publish" in drv)


# ── 4) Tracklist: track lẻ + khung thời gian trong mô tả ────────────────
def test_tracklist():
    print("\n[4] Mô tả có tracklist (track lẻ + mốc thời gian)")
    import json as _json
    import tempfile
    from backend.video import youtube_meta as ym

    # Mốc thời gian: video 2 tiếng → phải là H:MM:SS, không phải '117:23'.
    check("Mốc < 1 giờ dạng M:SS", ym._fmt_timestamp(184.9) == "3:04",
          ym._fmt_timestamp(184.9))
    check("Mốc >= 1 giờ dạng H:MM:SS (YouTube không nhận phút > 59)",
          ym._fmt_timestamp(7043.6) == "1:57:23", ym._fmt_timestamp(7043.6))

    # Đọc từ <stem>_info.json do bước Mix ghi ra.
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "mix.wav"
        wav.write_bytes(b"")
        (Path(d) / "mix_info.json").write_text(_json.dumps({"tracks": [
            {"name": "Deep Mountain", "start_seconds": 0.0},
            {"name": "Sacred Dawn", "start_seconds": 184.92},
            {"name": "Misty Sky", "start_seconds": 7043.59},
        ]}), encoding="utf-8")
        entries = ym.load_tracklist(str(wav))
        check("Đọc được track lẻ từ mix_info.json", len(entries) == 3,
              str(entries))
        check("Audio không qua bước Mix → không bịa tracklist",
              ym.load_tracklist(str(Path(d) / "upload.mp3")) == [])

    block = ym.format_tracklist(entries)
    lines = block.split("\n")
    check("Có tiêu đề tracklist", lines[0] == ym.TRACKLIST_HEADER, lines[0])
    check("Dòng đầu là 0:00 (điều kiện bật chapters)",
          lines[1].startswith("0:00 "), lines[1])
    check("Đủ tên + mốc của từng track lẻ",
          lines[1].endswith("01. Deep Mountain")
          and lines[2] == "3:04 02. Sacred Dawn"
          and lines[3] == "1:57:23 03. Misty Sky", " / ".join(lines[1:]))

    desc = "Line one.\n\nSubscribe to the channel.\n\n#zen #flute"
    out = ym.insert_tracklist(desc, block)
    check("Tracklist nằm trong mô tả", ym.TRACKLIST_HEADER in out)
    check("Hashtag vẫn ở dòng cuối", out.strip().split("\n")[-1] == "#zen #flute",
          out.strip().split("\n")[-1])
    check("Mô tả không vượt 5000 ký tự của YouTube",
          len(ym.insert_tracklist("x" * 4990, block)) <= ym.DESCRIPTION_LIMIT)

    # Nhiều track → cắt bớt nhưng vẫn báo rõ, không làm hỏng mô tả.
    many = [{"name": f"Track Name {i}", "start_seconds": i * 180.0}
            for i in range(400)]
    big = ym.format_tracklist(many)
    check("Danh sách quá dài → cắt gọn + ghi chú số còn lại",
          len(big) <= 3000 and "tracks)" in big, f"{len(big)} ký tự")

    # Không API key → vẫn phải có tracklist trong mô tả mặc định.
    meta = ym.generate_metadata(thumbnail_path="", channel_name="Bamboo Echoes",
                                instrument="bamboo flute", music_style="zen",
                                default_hashtags="#zen", past_titles=[],
                                api_config=None, tracklist=entries)
    check("Fallback (không API key) vẫn kèm tracklist",
          ym.TRACKLIST_HEADER in meta["description"])

    body = (Path(__file__).resolve().parents[1]
            / "backend/video/service.py").read_text(encoding="utf-8")
    check("step_upload_youtube truyền tracklist sang AI meta",
          "tracklist=tracklist" in body and "load_tracklist(audio)" in body)
    check("Nhạc nền lệch độ dài video → bỏ tracklist thay vì ghi sai giờ",
          "Bỏ tracklist" in body)


def main():
    print("=" * 62)
    print("VERIFY — Đăng nháp YouTube: thumbnail + chờ upload/quét chính sách")
    print("=" * 62)
    test_wait_upload_complete()
    test_thumbnail()
    test_wiring()
    test_tracklist()
    print("\n" + "=" * 62)
    print(f"PASS: {len(PASS)} | FAIL: {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ❌ " + f)
        print("❌ YOUTUBE UPLOAD FAILED")
        return 1
    print("✅ YOUTUBE UPLOAD ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
