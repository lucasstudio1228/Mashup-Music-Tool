# Mashup Music Tool

Công cụ tách nhạc thành **4 stem** (trống / bass / giọng hát / nhạc nền) bằng AI (Demucs),
chuẩn hoá âm lượng **−16 LUFS**, khử noise, chỉnh âm lượng từng stem theo thời gian thực,
và trộn (mashup) nhiều bài lại với nhau — tất cả trong một giao diện web chạy trên máy bạn.

---

## 🖥️ Yêu cầu máy

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10 / 11 |
| Python | **3.10 hoặc 3.11** ([tải tại đây](https://www.python.org/downloads/) — nhớ tick **Add Python to PATH**) |
| GPU | Khuyến nghị **NVIDIA** (CUDA) để tách nhạc nhanh. Không có GPU vẫn chạy được nhưng chậm hơn nhiều. |
| Dung lượng | ~3 GB cho thư viện + ~300 MB model AI (tự tải lần đầu) |

> ⚠️ Không cần cài Node.js — giao diện đã được build sẵn.

---

## 🚀 Cài đặt & sử dụng (3 bước)

**Bước 1 — Cài đặt (chỉ làm 1 lần):**
Double-click **`install.bat`**
Nó sẽ tự tạo môi trường ảo, cài PyTorch + Demucs và mọi thứ cần thiết. Lần đầu có thể mất 5–15 phút.

**Bước 2 — Chạy ứng dụng:**
Double-click **`start.bat`** (hoặc **`MashupMusicTool.exe`** nếu có).
Server khởi động và trình duyệt tự mở tại **http://localhost:8000**.

**Bước 3 — Dùng:**
Tạo project → tải nhạc lên → bấm tách stem → chỉnh âm lượng / trộn → xuất file.

> Để tắt ứng dụng: đóng cửa sổ đen (console) hoặc nhấn `Ctrl + C`.

---

## 🔑 Tùy chọn: API key cho tính năng AI

Một số tính năng gợi ý bằng AI cần **OpenAI API key**.
Mở file `.env` (được tạo tự động sau khi cài) và điền:

```
OPENAI_API_KEY=sk-...
```

Không dùng tính năng AI thì có thể bỏ qua bước này.

---

## 🧰 Xử lý sự cố

- **"Khong tim thay Python"** → Cài Python 3.10/3.11 và nhớ tick *Add to PATH*, rồi chạy lại `install.bat`.
- **Tách nhạc rất chậm** → Máy đang dùng CPU. Cần GPU NVIDIA + driver mới để tăng tốc.
- **Cổng 8000 bận** → Đóng ứng dụng khác đang dùng cổng 8000, hoặc sửa cổng trong `start.bat`.
- **Cài torch CUDA lỗi** → `install.bat` sẽ tự chuyển sang bản CPU. Muốn dùng GPU, cài lại torch CUDA thủ công.

---

## 🛠️ Dành cho lập trình viên

```bash
# Backend (Python)
pip install -r requirements.txt
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121

# Frontend (chỉ khi sửa giao diện)
cd frontend
npm install
npm run build        # tạo lại frontend/dist

# Chạy dev (backend :8000 + vite :5173 riêng)
python run.py --dev
```

Backend phục vụ luôn `frontend/dist` khi thư mục này tồn tại, nên bản phát hành chỉ cần 1 tiến trình / 1 cổng.
