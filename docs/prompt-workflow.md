# Quy trình prompt đã tích hợp vào Tool

## Sử dụng

1. Điền tên project, ý tưởng nhạc, ý tưởng hình ảnh, nhạc cụ/mục đích và phong cách. Các phần này hợp thành hồ sơ chung.
2. Trong Video, lưu ý tưởng rồi bấm **Chuẩn bị / kiểm tra bộ prompt**. Bước này chỉ gọi API viết chữ, không tạo nhạc/ảnh/video; vẫn có chi phí API văn bản nếu nhà cung cấp tính phí.
3. Mở **Xem bộ đã lưu** để xem hồ sơ nhân vật, bố cục và từng cặp prompt ảnh–motion. Tạo ảnh cũng tự chạy bước chuẩn bị này nếu chưa có hồ sơ hợp lệ.
4. Suno chuẩn bị một kế hoạch riêng cho từng lượt Create trước khi mở trình duyệt. Styles gốc giữ nhạc cụ/mood; các biến thể thay phrasing/arrangement. Khi resume, dùng đúng kế hoạch đã lưu và đúng lượt tiếp theo.
5. Tạo ảnh → kiểm tra nhân vật/bố cục thực tế → tạo clip. Cần tự duyệt mẫu bằng các bước riêng trước khi chọn chạy toàn bộ; Tool chưa có bộ đánh giá thị giác/âm nhạc tự động.

## Quy tắc đang thực thi

- Hồ sơ dùng chung lấy từ tên, mô tả, video_idea, suno_idea, instrument, music_style và Styles của batch Suno gần nhất.
- Mỗi project được cấp bộ gợi ý sáng tạo bền vững. Các bộ mới khác tối thiểu 3/6 trục gợi ý; yêu cầu cụ thể của người dùng được ưu tiên hơn gợi ý ngẫu nhiên. Đây không phải phép đo độ khác biệt ngữ nghĩa của thành phẩm.
- Nhân vật và scene_sheet được dán nguyên văn vào mọi prompt ảnh. Camera/bố cục/thời điểm được khoá. Cảnh nền là vi trạng thái có thể đảo thứ tự, không còn ép hành trình hay sáng–trưa–tối.
- Motion theo từng ảnh: chuyển động nhỏ, chỉ các yếu tố có trong ảnh, camera cố định, không fade trong clip nguồn. Fade giữ ở bước ghép.
- Bộ mới phải đủ N prompt ảnh và N motion, đúng khóa 0..N-1, không trống/không lặp nguyên văn trong bộ. Thiếu motion được gọi AI bổ sung riêng; vẫn thiếu thì dừng trước media.
- Suno dùng Lyrics rỗng/instrumental theo driver hiện có; Exclude bổ sung các loại giọng hát. Styles cuối không quá 1.000 ký tự; quá dài sẽ báo lỗi, không tự cắt. Styles gốc nên dưới 600 ký tự để chừa chỗ cho biến thể.
- Khi AI viết Styles thất bại, không âm thầm dùng preset khác ý tưởng. Có thể nhập Styles đã duyệt thủ công. Preset chưa sửa là đầu vào tham khảo, không được xem là một bản nhạc riêng đã duyệt.
- Registry SQLite dùng transaction và hash chuẩn hoá Unicode/hoa-thường/khoảng trắng để chặn prompt nguyên văn trùng project khác. Cùng project được resume. Không bảo đảm chặn mọi diễn đạt đồng nghĩa hoặc bảo đảm AI tạo media khác nhau.
- Lịch sử cũ được nhập một lần trước khi claim đầu tiên. Các bản trùng lịch sử được giữ để project cũ chạy tiếp; project mới không được chiếm lại. File chép tay sau lần nhập đầu không tự nhận quyền sở hữu.
- Registry gắn owner với ID và created_at, giữ lại khi xoá project để ID tái sử dụng không được kế thừa prompt cũ.
- Manifest mới lưu nguyên tử và gắn dấu nhận diện đầu vào. Đổi ý tưởng/phong cách/số lượng trong lúc còn ảnh sẽ chặn resume; phải chủ động chọn làm lại toàn bộ. Không thay ảnh/media cũ trong lần nâng cấp này.

## Số lượng và thumbnail

Giữ cấu hình hiện có: **41 ảnh / 41 clip**, T+10; không tự đổi về 20. Đã bổ sung đủ prompt dự phòng 0..40 và loại fallback cảnh rời rạc khi thiếu key. Bộ cũ 20 ảnh không được mặc nhiên xem là đủ 41; cần chủ động mở rộng/làm lại trước khi chạy toàn bộ theo cấu hình hiện tại.

Ảnh 0 là nền sạch của thiết kế thumbnail; title/subtitle được overlay khi ghép lên intro và thumbnail.png. Clip từ ảnh 0 chỉ ở đầu một lần, không thuộc cycle lặp. Logic này được kiểm thử ở service và assembler thật (FFmpeg được mock trong test).

Không thay model, độ phân giải tải xuống, lịch upload hoặc tự tiêu credit trong lần cập nhật này. Cấu hình Flow hiện tại vẫn tải 720p; xuất final 1080p không đồng nghĩa nguồn tải native 1080p. Đây không phải một hạng mục đã sửa trong nhóm 1, 2, 7, 8.

## Dữ liệu và khôi phục

- Hồ sơ ảnh/motion: `media/<tên> (#<id>)/images/prompts.json`.
- Kế hoạch nhạc: `SunoBatch.config_json` trong dữ liệu ứng dụng.
- Lịch sử chống trùng và gợi ý project: `data/prompt_catalog.sqlite3`. Cần sao lưu cùng app.db và media.
- Xoá project chỉ dọn thư mục do project sở hữu: media theo ID/tên+ID, outputs, stems và Suno staging; dọn các bảng con liên quan trước Project.
- Không lấy Track.filepath/Mix.output_dir tùy ý làm đích xoá. Nhạc gốc import từ bên ngoài được giữ. Thư mục legacy chỉ có tên mà không có ID không bị đoán quyền sở hữu để xoá.
- Tác vụ đang chạy/chờ hoặc Suno còn lock sẽ bị từ chối xoá. File được chuyển tạm vào quarantine trong đúng vùng dữ liệu, rollback nếu DB lỗi. Sau commit mới purge; nếu purge lỗi, frontend cảnh báo và giữ đường dẫn trong `data/project_deletions/.../manifest.json`.
- Dọn xong là xoá vĩnh viễn; quarantine chỉ dùng để xử lý lỗi, không phải chức năng thùng rác cho người dùng.
- Busy guard bảo vệ worker đã đăng ký; Tool vẫn là ứng dụng single-process local, chưa triển khai khoá vòng đời project liên tiến trình cho các server chạy song song.

## Kiểm thử và checkpoint

Chạy `python -m unittest discover -s tests -p 'test_*.py' -v`, `python tests/verify_suno.py`, và `npm run build` trong frontend. Các kiểm thử dùng thư mục/DB tạm, AI và trình duyệt giả lập; không Create trên dịch vụ thật và không xoá project thật.

Checkpoint trước thay đổi: `82700fd`. Phiên đăng nhập, .env, app.db và media không được commit. File `0.05` có sẵn, không thuộc thay đổi workflow và được giữ nguyên.

Kết quả xác minh 22/09/2026: 29 unit/integration tests chạy, 28 pass và 1 skip do Windows không cấp quyền tạo symlink thật; nhánh phát hiện thuộc tính junction/reparse có test mô phỏng riêng đã pass. Bộ verify_suno (WAV, preset, dry-run guard) pass. Frontend TypeScript/Vite build pass. API chuẩn bị/xem prompt đã xuất hiện trên server thật; hồ sơ cũ vẫn đọc được. Đã nhập 5 nguồn lịch sử vào registry, không bỏ sót nguồn nào. Chưa chạy tạo nhạc/ảnh/video trả phí để đánh giá chất lượng AI thực tế.
