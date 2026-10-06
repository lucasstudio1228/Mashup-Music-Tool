# Mashup Music Tool — Healing / Meditation YouTube

Công cụ chạy trên máy bạn (giao diện web `http://localhost:8000`) để làm video nhạc
thiền / chữa lành / lofi cho YouTube, từ nhạc tới bản nháp YouTube:

1. **Nhạc (Suno)** — tự tạo và tải các bài WAV theo nhạc cụ + thể loại bạn chọn.
   Bạn cũng có thể tải nhạc của mình lên.
2. **Mix** — trộn thành 1 bản dài (vd 120 phút) có crossfade, chuẩn hoá −16 LUFS.
   Có bước rà soát chất lượng từng bài.
3. **Video** — vẽ ảnh bìa + 2 bảng nhân vật bằng **Gemini**, tạo clip 8 giây bằng
   **Muse.ai** (hoặc Gemini/Flow), rồi ghép thành `final.mp4` khớp độ dài nhạc.
4. **YouTube** — tải lên dưới dạng **bản nháp** (không bao giờ tự công khai) qua GPMLogin.
5. **Sản xuất hàng loạt** — nhập số lượng N: AI nghĩ N ý tưởng khác nhau (cùng nhạc
   cụ / thể loại / style ảnh) và chạy tuần tự trọn chuỗi cho từng sản phẩm.

Ngoài ra còn công cụ tách **4 stem** (Demucs), khử noise và mashup nhiều bài.

---

## 🖥️ Yêu cầu

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10 / 11 |
| Python | **3.10 hoặc 3.11** ([python.org](https://www.python.org/downloads/) — tick **Add Python to PATH**) |
| ffmpeg | Bắt buộc, phải có trong PATH: `winget install Gyan.FFmpeg` |
| GPU | Khuyến nghị NVIDIA (chỉ cần cho tách stem Demucs) |
| OpenAI API key | Dùng để viết prompt, ý tưởng, tiêu đề (nhập trong ⚙️ Settings) |
| Tài khoản | Google (Gemini), Suno, YouTube — **bạn tự đăng nhập** trong cửa sổ trình duyệt tool mở ra |
| GPMLogin *(tuỳ chọn)* | Cho đăng YouTube và Muse.ai — Local API mặc định `http://localhost:9495` |
| Cốc Cốc *(tuỳ chọn)* | Cho Suno và Google Flow (tool tự dò nếu có cài) |

> Không cần Node.js — giao diện đã build sẵn trong `frontend/dist`.

---

## 🚀 Cài đặt & chạy

```bat
git clone https://github.com/lucasstudio1228/Mashup-Music-Tool.git
cd Mashup-Music-Tool
```

1. Double-click **`install.bat`** (chạy 1 lần, khoảng 5–15 phút). Script này sẽ:
   - tạo `.venv`;
   - cài PyTorch, thư viện Python và Chromium cho Playwright;
   - kiểm tra ffmpeg;
   - tạo file `.env`.
2. Double-click **`start.bat`**. Trình duyệt tự mở `http://localhost:8000`.
3. Vào **⚙️ Settings** và nhập **OpenAI API key** (hoặc điền `OPENAI_API_KEY` trong `.env`).
4. *(Đăng YouTube)* Trong mục YouTube, thêm **kênh**: tên kênh + UUID profile GPMLogin
   đã đăng nhập kênh đó, cùng nhạc cụ / thể loại tương ứng.
5. *(Clip bằng Muse.ai)* Tạo file `data/video_overrides.json` theo mẫu
   [`docs/video_overrides.example.json`](docs/video_overrides.example.json) và điền
   `muse_gpm_profile_id` = UUID profile GPMLogin đã đăng nhập muse.ai.

Lần đầu chạy Gemini / Suno, tool mở cửa sổ trình duyệt riêng và **chờ bạn tự đăng
nhập**. Phiên đăng nhập được giữ lại trong `.browser_profile*` trên máy bạn.

> Tắt ứng dụng: đóng cửa sổ console đen (hoặc `Ctrl + C`). Đã sửa code backend thì
> phải tắt rồi mở lại `start.bat`.

---

## 🔒 Dữ liệu cá nhân — KHÔNG có trong repo

Các thư mục / file sau chỉ nằm trên máy bạn và đã được `.gitignore` loại trừ:

- `.env` — API key.
- `data/` — CSDL `app.db`, cấu hình, log, nhạc tạm của Suno.
- `media/`, `outputs/`, `stems/`, `Projects/` — nhạc, ảnh, video.
- `.browser_profile*/` — phiên đăng nhập trình duyệt.

Đừng commit các thư mục này.

---

## 🧰 Xử lý sự cố

- **"Khong tim thay Python"** → cài Python 3.10/3.11 (tick *Add to PATH*), chạy lại `install.bat`.
- **Lỗi ghép video / `ffmpeg` not found** → `winget install Gyan.FFmpeg`, mở lại `start.bat`.
- **Gemini trả chữ thay vì ảnh** → tool tự bật công cụ «Tạo hình ảnh» và tự thử lại.
  Nếu vẫn lỗi, bấm «Tạo tiếp ảnh thiếu»; các ảnh đã có không bị mất.
- **Cổng 8000 bận** → đóng ứng dụng đang dùng cổng 8000, hoặc sửa cổng trong `start.bat`.
- **Torch CUDA lỗi** → `install.bat` tự chuyển sang bản CPU (tách stem chậm hơn).

---

## 🛠️ Dành cho lập trình viên

```bat
:: Test
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

:: Sửa giao diện → build lại frontend/dist (cần Node.js)
cd frontend
npm install
npm run build

:: Chạy dev (backend :8000 + vite :5173)
.venv\Scripts\python.exe run.py --dev
```

Tài liệu thêm: [`VIDEO_MODULE.md`](VIDEO_MODULE.md) và thư mục [`docs/`](docs/).
