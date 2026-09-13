# Module Video (Audio ↔ Video)

Tự động: **tạo 15 ảnh liên kết (Gemini) → tạo 15 clip 8s 1:1
(Flow/Veo 3.1 Fast, Ingredients) →
shuffle T+5 theo duration audio → ghép + gắn nhạc → MP4**.

## Cài đặt thêm (1 lần)
```bat
.venv\Scripts\pip.exe install playwright
.venv\Scripts\playwright.exe install chromium
```
> Có thể dùng Chrome sẵn có thay vì Chromium: để `chrome_channel="chrome"`
> trong `backend/video/config.py` (mặc định đã vậy).

## Cấu trúc thư mục (mỗi project 1 folder)
```
media/<project_name>/
  images/  0.png (thumbnail có chữ) ... 14.png (15 ảnh liên kết)
  clips/   clip_00.mp4 ... clip_14.mp4        (15 clip, ánh xạ 1:1)
  final/   final.mp4                          (video hoàn chỉnh)
.browser_profile/                             (giữ đăng nhập Gemini/Flow)
data/video_overrides.json                     (tùy chọn: override selector/prompt)
```

## Quy trình
1. **Ảnh** — `POST /api/projects/{id}/video/images` → mở Gemini, tạo 15 shot
   hoạt hình chill 16:9 trong cùng một câu chuyện. Tất cả shot giữ nguyên nhân
   vật, cabin, đạo cụ, bảng màu và ánh sáng diễn tiến. Ảnh 0 là thumbnail có
   tiêu đề project cùng bộ từ khóa ngẫu nhiên như Meditation, Zen, Ambient,
   Focus, Study, Healing…; ảnh 1–14 không có chữ.
2. **Clip** — `POST /api/projects/{id}/video/clips` → mở Flow, upload 15 ảnh,
   chọn Ingredients, Veo 3.1 Fast, 16:9, 8s, x1; tạo **tuần tự 15 clip 1:1**.
   Clip đầu tiên bắt buộc dùng ảnh 0; mỗi ảnh 1–14 dùng đúng một lần. Chỉ gửi
   lượt kế khi clip trước đã render xong. Mỗi clip được upscale và tải bản
   1080p; driver chờ job upscale hoàn tất rồi mở menu Download lần thứ hai.
3. **Ghép** — `POST /api/projects/{id}/video/assemble` → bắt đầu bằng
   `clip_00`, sau đó random T+5 (không lặp lại khi chưa có đủ 5 video khác xen
   giữa) để phủ duration của `mix.wav`. Video được làm chậm 0.7×, nối bằng
   transition `fade` 1 giây, gắn nhạc và xuất `final/final.mp4`.
   - Hoặc chạy trọn gói: `POST .../video/run-all`.
4. Theo dõi: SSE `GET .../video/progress`. Trạng thái: `GET .../video/status`.
   Tải về: `GET .../video/download`.

## ⚠️ Tinh chỉnh selector (quan trọng)
Gemini & Flow đổi giao diện thường xuyên. Khi automation báo lỗi kiểu
"không tìm thấy … (selector 'xxx')", mở `backend/video/config.py`, sửa trong
`GEMINI_SELECTORS` / `FLOW_SELECTORS` (mỗi mục là danh sách selector thử lần
lượt). Có thể override không cần sửa code bằng `data/video_overrides.json`:
```json
{
  "selectors": {
    "flow": { "generate_button": ["button:has-text('Tạo video')"] }
  },
  "prompts": { "0": "prompt thumbnail của bạn ..." },
  "flow_motion_prompt": "camera lia chậm ..."
}
```

## Lần chạy đầu
Trình duyệt mở ra (không headless). **Tự đăng nhập Gemini và Flow** trong cửa
sổ đó — session lưu vào `.browser_profile`, các lần sau khỏi đăng nhập lại.

## Đã kiểm chứng
- Shuffle T+N và ghép ffmpeg (nối clip + cắt đúng duration audio + gắn nhạc,
  bỏ tiếng gốc của clip) đã test chạy đúng.
- Selector Flow tiếng Anh/Việt và cơ chế tạo tuần tự đã được kiểm chứng trực tiếp.
