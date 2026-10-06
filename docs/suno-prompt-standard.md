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

## 2. Form chuẩn (thứ tự cố định, code tự ghép — tiếng Anh)
```
{genre}, {mood}. Lead: {lead}. Support: {support}. Tempo: {bpm} BPM, {groove}.
Key: {key}. Chord progression: {progression}. Melody: {melody}.
Structure: {structure}. Mix: {mix}. Purely instrumental, no vocals.
```
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
Model v6 · Advanced · Lyrics để trống (instrumental) · Duration Auto (mục tiêu 3–5 phút,
≥ 2 phút) · Max Mode Off · Weirdness mặc định · tải WAV · tối thiểu credit.
