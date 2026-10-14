# Chuẩn prompt Suno (Styles) — quy định cho AI viết nhạc

Nguồn sự thật trong code: `backend/video/music_spec.py` (nhạc cụ, thể loại, form) và
`backend/video/suno_prompt.py` (AI điền form → code ghép + kiểm tra → biến thể từng lượt Create).

## 1. Đầu vào AI bám theo
| Trường project | Vai trò |
|---|---|
| **Nhạc cụ** (New Project → chọn) | Lead DUY NHẤT; không bao giờ bị thay; không bao giờ nằm trong Exclude |
| **Thể loại nhạc** | Chọn hồ sơ thể loại: dải BPM, có/không nhịp, hoà âm, màu đệm, mix, exclude |
| **Name + Description** | Không khí, bối cảnh, câu chuyện → mood, melody, structure |
| Ý tưởng nhạc Suno / Styles đã duyệt | Bổ sung, không được mâu thuẫn nhạc cụ/thể loại |

## 2. Form chuẩn v2 (2026-10-08 — thứ tự cố định, code tự ghép — tiếng Anh)
```
Lead: {lead}. {genre}, {mood}. The {short} alone carries the melody: {melody}.
Key: {key}. {harmony}. Tempo: {bpm} BPM, {groove}. Background, very quiet: {support}.
Structure: {structure}. Mix: {mix}. Purely instrumental, no vocals.
```
- Nhạc cụ chính **đứng đầu** và được nhắc lại bằng tên ngắn (`{short}`, vd "flute").
- `{harmony}`: lead chơi được hợp âm (piano, guitar, harp, guzheng…) → `Harmony on the {short}: {progression}`;
  lead **đơn âm** (sáo, nhị, cello…) → `Harmony: a single faint low drone beneath the {short}` — KHÔNG gửi vòng
  hợp âm (Suno sẽ tự thêm đàn đệm).
- `support` = chỉ âm thanh thiên nhiên / không gian / tối đa 1 drone pad (+ trống/bass nhẹ nếu thể loại có nhịp) —
  không bao giờ nhạc cụ có cao độ (chuông, bát, đàn gảy, đàn kéo, phím).
- Exclude: chống giọng + exclude thể loại + **mọi họ nhạc cụ giai điệu khác lead** (`music_spec.build_exclusions`).
- Plan cũ form v1 (đã đóng băng trong batch) được tự nâng lên v2 khi chạy tiếp / tạo lại bài
  (`suno_service.upgrade_plan_v2`, bản cũ giữ ở `prompt_plan_v1`).

| Trường | Quy định |
|---|---|
| genre | 2–5 từ, tag thể loại con (vd `Lofi chillhop`) |
| mood | 2–4 tính từ |
| lead | 1 nhạc cụ solo + âm sắc/kỹ thuật; PHẢI là nhạc cụ project (thiếu → code tự chèn) |
| support | 2–4 lớp nền nhẹ; trống/bass CHỈ khi thể loại có nhịp |
| bpm | 1 số nguyên, code kẹp vào dải thể loại |
| groove | cảm giác nhịp (`free-flowing rubato`, `laid-back swing`) |
| key | `D major`, `A minor`, `E minor pentatonic`, `F Lydian` |
| progression | 3–6 hợp âm hợp lệ (`Fmaj7 – Dm9 – Bbmaj7 – C7`) |
| melody | quãng, câu nhạc, luyến láy, âm vực |
| structure | bài 3–5 phút, không cao trào/drop |
| mix | reverb, độ rộng stereo, tape/vinyl, EQ |
| exclusions | luôn có bộ chống giọng + exclude thể loại; không loại nhạc cụ lead |

Toàn bộ Styles gốc ≤ **600** ký tự (chừa chỗ cho chữ ký project + biến thể; ô Suno tối đa 1000).
Sai key / hợp âm / thiếu trường / quá dài / thể loại không nhịp mà có trống → AI phải viết lại (tối đa 3 lần).

## 3. Hồ sơ thể loại
| Thể loại | BPM | Nhịp | Hoà âm |
|---|---|---|---|
| Lofi / chillhop | 68–88 | trống boom-bap nhẹ, sub bass ấm | 7th/9th jazz |
| Zen Trung Hoa | 50–66 | không nhịp | ngũ cung, điệu cung/vũ |
| Deep sleep | 44–60 | không nhịp | trưởng/Lydian, rất êm |
| Ambient | 50–70 | không nhịp | trưởng/Lydian/Dorian |
| Spa / healing | 55–72 | không nhịp | trưởng, ngũ cung trưởng |
| Meditation (mặc định) | 50–68 | không nhịp | ngũ cung/Dorian/Lydian |

## 4. Biến thể mỗi lượt Create (15 bài không na ná)
Giữ lead + không khí; mỗi lượt đổi BPM (trong dải thể loại), dời giọng (dời cả vòng hợp âm),
1 màu đệm từ thể loại, motif + arc riêng.

## 5. Thông số Suno do tool đặt cố định (AI không đổi)
Model **v6** (không dùng custom model) · Advanced · Lyrics **trống** (instrumental; không dùng structure tag) ·
Duration Auto (mục tiêu 3–5 phút, ≥ 2 phút) · **Max Mode On** (luôn bật, xem §8) · More Options:
**Variety 0 (Off)** · **Style Influence 75** · **Weirdness 30** (`SunoConfig.variety/style_influence/weirdness`;
thanh trượt chỉ kéo bằng chuột, tool đọc lại và kiểm lại TRƯỚC MỖI Create) · tải WAV · tối thiểu credit.

## 6. Chọn 1 trong 2 bài của mỗi lượt Create (0 credit)
Mỗi Create ra 2 bài. Sau khi tải WAV bài đầu, tool chấm "đúng nhạc cụ chính" bằng PANNs Cnn14
(`video/instrument_qc.py`, ~0,4 s/bài trên GPU): **sạch** = nhạc cụ chính rõ ≥ 60% thời lượng, không họ nhạc cụ
nào khác > 15%, giọng người ≤ 5%. Bài đầu lẫn nhạc cụ **hoặc** ngoài 3–5 phút → tải bài thứ 2 rồi giữ bài tốt hơn:
1. bài **sạch** thắng bài lẫn (vẫn bắt buộc ≥ 2 phút);
2. cả 2 cùng lẫn mà một bài lẫn ít hơn rõ (≥ 0,25) → bài đó;
3. gần 3–5 phút hơn; 4. hoà → lẫn ít hơn.
Kết quả lưu ở `SunoCandidate.content_qc_status` = `clean` / `leaky`. Thiếu trọng số PANNs (~/panns_data) hay nhạc cụ
chưa có trong `instrument_qc.LEAD_FAMILIES` → tự bỏ qua, chọn theo độ dài như trước. Luồng «Tạo lại bài lỗi»
(`suno_fix`) dùng cùng tiêu chí khi chọn bài thay thế. Tắt: override `instrument_qc=false`.

## 7. Bằng chứng A/B (2026-10-08, `data/_suno_ab.py`)
Project #24 (Bamboo Flute / Chinese Zen), mỗi nhánh 3 Create = 6 bài, chấm bằng `instrument_qc`:

| Nhánh | Prompt | Variety / SI / Weird | Bài sạch | Lẫn TB |
|---|---|---|---|---|
| A | v1 (cũ) | 1 / 50 / 50 (mặc định) | 0% | 0,674 |
| B | v1 | 0 / 75 / 30 | 0% | 0,576 |
| C | v2 | 1 / 50 / 50 | 33% | 0,466 |
| **D (chọn)** | **v2** | **0 / 75 / 30** | **67%** | **0,335** |
| G | v2 | 0 / 90 / 15 | 50% | 0,357 |
| E | v2 + tag lời `[Instrumental]…` | 0 / 75 / 30 | 33% | 0,415 |
| F | v2 + custom model "Bamboo Flute v1" | 0 / 75 / 30 | 67% | 0,40 |

Chọn 1 trong 2 bài theo chấm nhạc cụ (15 Create nhánh v2): bài đầu sạch 47% → chọn tốt hơn sạch **73%**.

Vòng 2 (2026-10-08, chấm `instrument_qc`, nhánh 3 Create): project #21 (Zen Guitar) — A (v1, 1/50/50):
sạch 67%, lẫn TB 0,178 · **D (v2, 0/75/30): sạch 100%, lẫn TB 0,023** · G (v2, 0/90/15): sạch 100%, lẫn 0,05.
Project #18 (Lofi Piano), chấm đủ 6 bài/nhánh 2026-10-08 (tải WAV qua Studio): bản production cũ sạch 0%, lẫn 0,717 · A: sạch 0%, lẫn 0,684 · **D: sạch 0%, lẫn 0,466** (giảm 35%, nhưng tỉ lệ piano dẫn chỉ 0,51) · G: sạch 0%, lẫn 0,821. Phần «lẫn» chủ yếu là họ `synth` — có thể PANNs đọc nhầm chất lofi (tape, warm filter) thành Synthesizer; cần nghe kiểm.
#21 bổ sung đủ 6/6 bài: D vẫn sạch 100%, lẫn 0,019.

## 8. Max Mode — LUÔN bật (chốt 2026-10-08)
- Vị trí: More Options → hàng «Max Mode» có 2 nút **Off / On** (`data-selected`). Tool chỉ bấm nút TRONG hàng
  đó (`SunoDriver.set_max_mode` đánh dấu nút rồi bấm, đọc lại trạng thái; không dính nút "On" khác như Personalize).
- Tải lại `/create` → Suno trả về **Off** ⇒ tool đặt lại On trước MỖI lượt Create.
- Tooltip Suno: «Uses more compute to maximize consistency throughout the song. Costs 2x credits per song.»
  ⇒ **20 credit / lượt Create** (`SunoConfig.credits_per_create`; đo thật 10 khi Off). Còn < 20 credit → dừng.
- Đo thật 2026-10-08 (`data/_suno_maxmode_live.py`, v6 + Max Mode On + prompt D #21): 6300 → 6280 = **20 credit**,
  không có hộp xác nhận nào sau khi bấm Create, 2 bài xuất hiện ngay.
- Không cho tắt từ giao diện; override cũ `max_mode=false` bị bỏ qua.

## 9. Hạn mức TẢI của Suno (từ 03/09/2026)
- Tải THƯỜNG (⋯ → Download từ trang bài): Free 7 bài trọn đời · Pro 20 bài/tháng · **Premier 60 bài/tháng**.
- Suno: gói **Premier dùng Suno Studio không giới hạn lượt tải** ⇒ đây chính là lý do production đi
  Studio → Export → Full Song → Go to Song → Download → **WAV** (`SunoDriver.download_wav`).
  Dữ liệu thật: 30/09–07/10 tool tải 276 WAV qua Studio, không trừ lượt nào; «Downloads Remaining» chỉ về 0
  sau đúng 60 MP3 tải thường cho A/B (08/10).
- **Đo thật 2026-10-08** (`data/_probe_studio_wav.py`): tài khoản Premier báo **0** «Downloads Remaining» vẫn tải
  WAV qua Studio bình thường (214 s, 48 kHz stereo, hợp lệ, 94 s/bài); sau khi tải vẫn 0 → không bị trừ lượt.
- Vì vậy tool **không** chặn Create theo con số «Downloads Remaining» ở suno.com/account (gate
  `check_download_allowance` mặc định tắt; chỉ bật nếu đổi sang tải thường).
- Hộp thoại «Out of Downloads» giữa chừng (nếu gói đổi, hoặc Suno đổi chính sách) → đóng bằng Escape, dừng batch
  (`INSUFFICIENT_DOWNLOAD_ALLOWANCE`), bài giữ trạng thái chờ (resume sau khi xử lý). **Không bao giờ** bấm
  «Buy download packs».
- **CHỈ tải WAV qua Studio, KHÔNG BAO GIỜ tải MP3/tải thường** (chốt 2026-10-08): driver không còn đường tải
  MP3; harness A/B `data/_suno_ab.py dl` cũng tải WAV qua Studio. Test `test_no_mp3_or_normal_download_path` giữ chốt này.

