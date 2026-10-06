export interface Project {
  id: number;
  name: string;
  description: string;
  created_at: string;
  track_count?: number;
  mix_count?: number;
  video_idea?: string;
  auto_video?: boolean;
  video_style?: string;
  instrument?: string;
  music_style?: string;
  auto_upload?: boolean;
  music_source?: string;   // "local" | "suno"
  suno_idea?: string;      // ý tưởng/nhạc cụ cho STEP 0 (Suno)
  batch_id?: string | null; // nhóm project cùng 1 lô sản xuất hàng loạt
  mix_duration_minutes?: number;  // tổng thời lượng mix = độ dài video
  mix_crossfade_seconds?: number;
}

// ---------- Sản xuất hàng loạt (batch) ----------
export interface BatchCreate {
  count: number;
  instrument: string;       // nhạc cụ — giữ nguyên cả lô (bắt buộc)
  music_style: string;      // thể loại nhạc — giữ nguyên cả lô (bắt buộc)
  channel_name?: string;
  video_style?: string;     // key phong cách ảnh (2d/3d/ghibli/…/real)
  theme?: string;           // chủ đề tuỳ chọn
  target_tracks?: number | null;
  max_create_actions?: number | null;
  mix_duration_minutes?: number;  // tổng thời lượng mix/video mỗi project
}

export interface InstrumentOption {
  key: string;
  label: string;     // nhãn tiếng Anh (lưu vào project)
  vi: string;        // nhãn hiển thị tiếng Việt
  portable: boolean;
}

export interface GenreOption {
  key: string;
  label: string;
  bpm: [number, number];
  beatless: boolean;
}

export interface BatchStyleOption {
  key: string;
  icon: string;
  label: string;
  desc: string;
}

export interface BatchProjectItem {
  project_id: number;
  name: string;
  suno_phase?: string | null;
  suno_message?: string | null;
}

export type BatchStatus = 'running' | 'completed' | 'cancelled' | 'blocked' | 'failed';

export interface BatchRun {
  batch_key: string;
  total: number;
  completed: number;
  failed: number;
  status: BatchStatus;
  current_index: number;
  current_project_id?: number | null;
  message: string;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  projects: BatchProjectItem[];
}

export interface AppSettings {
  api_base_url: string;
  api_model: string;
  has_api_key: boolean;
}

export interface AppSettingsUpdate {
  api_key?: string;
  clear_api_key?: boolean;
  api_base_url?: string;
  api_model?: string;
}

export interface ApiTestRequest {
  api_key?: string;
  api_base_url?: string;
  api_model?: string;
}

export interface ApiTestResult {
  ok: boolean;
  model: string;
  base_url: string;
  latency_ms?: number | null;
  reply?: string | null;
  error_type?: string | null;
  error?: string | null;
  retryable: boolean;
}

export interface Track {
  id: number;
  project_id: number;
  filename: string;
  filepath: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  format: string;
  is_lossy: boolean;
  subtype?: string;
  added_at: string;
}

export type MixStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';

export interface Mix {
  id: number;
  project_id: number;
  title: string;
  status: MixStatus;
  created_at: string;
  completed_at?: string;
  duration_minutes: number;
  crossfade_seconds: number;
  sample_rate: number;
  bit_depth: number;
  output_dir?: string;
  total_duration_seconds?: number;
  track_count?: number;
  error_message?: string;
  progress_percent?: number;
  progress_step?: number;
  progress_step_name?: string;
}

export interface ProjectDetail extends Project {
  tracks: Track[];
  mixes: Mix[];
}

export interface ScanResult {
  added: number;
  skipped_duplicates: number;
  skipped_errors: number;
  error_files: string[];
  total_tracks: number;
  warning_lossy: boolean;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  instrument?: string;     // nhạc cụ chủ đạo — khoá nhân vật/cảnh/Styles Suno
  music_style?: string;    // thể loại nhạc (lofi, zen, sleep…)
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  video_idea?: string;
  auto_video?: boolean;
  video_style?: string;
  instrument?: string;
  music_style?: string;
  auto_upload?: boolean;
  music_source?: string;
  suno_idea?: string;
  mix_duration_minutes?: number;
  mix_crossfade_seconds?: number;
}

export interface MixCreate {
  duration_minutes: number;
  crossfade_seconds: number;
  sample_rate: number;
  bit_depth: number;
  title?: string;
}

export interface MixProgressEvent {
  step: number;
  step_name: string;
  percent: number;
  message?: string;
}

export type StemStatus =
  | "not_started" | "pending" | "running"
  | "completed"   | "failed";

export interface StemVolumes {
  other:  number;   // Âm nhạc — 0.0 → 2.0
  bass:   number;   // Âm trầm
  drums:  number;   // Trống
  vocals: number;   // Giọng hát
}

export interface StemDenoise {
  other:  number;   // Độ mạnh khử noise thủ công — 0.0 → 1.0
  bass:   number;
  drums:  number;
  vocals: number;
}

export interface TrackStemInfo {
  track_stem_id?:     number;
  status:             StemStatus;
  error_message?:     string;
  volumes:            StemVolumes;
  denoise?:           StemDenoise;
  waveform_data?:     Record<string, number[]> | null;
  lufs_gain_applied?: number;
}

export interface ProjectStemStatus {
  total:            number;
  counts: {
    pending:   number;
    running:   number;
    completed: number;
    failed:    number;
  };
  is_processing:    boolean;
  pending_in_queue: number;
}

// ── Video ──────────────────────────────────────────────────────
export interface VideoParamsInfo {
  image_count:    number;   // 3: bìa + bảng nhân vật chính + bảng linh thú
  // Nhãn vai trò từng ảnh, khoá là chỉ số ảnh dạng chuỗi ("0" | "1" | "2").
  image_roles?:   Record<string, string>;
  clip_seconds:   number;
  rest_clips:     number;
  total_clips:    number;
  t_window:       number;
  model:          string;
  clip_engine?:   string;   // "flow" (mặc định) | "gemini" | "muse"
  clip_engine_label?: string;
  clip_engines?:  { key: string; label: string }[];
  aspect_ratio:   string;
  flow_mode:      string;
  blend_seconds:  number;
  slow_speed:     number;
}

export type VideoJobStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';

export interface VideoJobInfo {
  kind:    string;   // images | clips | assemble | full
  status:  VideoJobStatus;
  percent: number;
  message: string;
  error?:  string | null;
}

export interface VideoStatus {
  images:         string[];
  image_count:    number;
  image_indices?: number[];
  clips:          string[];
  clip_count:     number;
  clip_indices?:  number[];
  final_exists:   boolean;
  final_path?:    string | null;
  thumbnail_path?: string | null;
  audio_path?:    string | null;
  audio_duration?: number | null;
  job?:           VideoJobInfo | null;
  params:         VideoParamsInfo;
  // Trạng thái phiên "Tạo video thủ công" (null = chưa chạy lần nào).
  manual?: {
    active: boolean; phase: string; current: number | null; prompt: string;
    ingredients: number[]; blocked: string | null; total: number;
    done: number[]; todo: number[]; message: string;
  } | null;
}

// ── Suno (STEP 0 — tạo nhạc nền) ───────────────────────────────
export interface SunoPreset {
  key:        string;
  label:      string;
  styles:     string;      // gửi sang Suno (English)
  exclusions: string;
}

export interface SunoDefaults {
  source:                          string;   // "suno" | "local"
  preset:                          string;
  target_tracks:                   number;
  preferred_model:                 string;
  allow_model_fallback:            boolean;
  instrumental:                    boolean;
  max_mode:                        boolean;
  preferred_duration_seconds_min:  number;
  preferred_duration_seconds_max:  number;
  minimum_duration_seconds:        number;
  max_create_actions:              number;
  max_generation_credits:          number;
  max_new_song_downloads:          number;
  auto_continue_workflow:          boolean;
}

export interface SunoConfigResponse {
  defaults: SunoDefaults;
  presets:  SunoPreset[];
}

// Phase khớp backend.video.suno_service (PREFLIGHT..COMPLETED + blocking states).
export type SunoPhase =
  | 'PREFLIGHT' | 'GENERATING' | 'DOWNLOADING' | 'VALIDATING'
  | 'READY_FOR_IMPORT' | 'IMPORTED' | 'DOWNSTREAM_RUNNING' | 'COMPLETED'
  | 'WAITING_FOR_LOGIN' | 'WAITING_FOR_HUMAN' | 'SUBMISSION_UNCERTAIN'
  | 'INSUFFICIENT_GENERATION_CREDITS' | 'INSUFFICIENT_DOWNLOAD_ALLOWANCE'
  | 'UI_CHANGED' | 'FAILED' | 'PAUSED' | 'CANCELLED';

export interface SunoBatchStatus {
  batch_id:             number;
  project_id:           number;
  phase:                SunoPhase;
  message?:             string | null;
  dry_run:              boolean;
  credits_remaining?:   number | null;
  preset?:              string | null;
  model?:               string | null;
  target_tracks:        number;
  generated_candidates: number;
  selected_tracks:      number;
  downloaded_tracks:    number;
  validated_tracks:     number;
  imported_tracks:      number;
  create_actions_used:  number;
  staging_dir?:         string | null;
}

export interface SunoJobInfo {
  kind:    string;   // "suno"
  status:  VideoJobStatus;
  percent: number;
  message: string;
  error?:  string | null;
}

// Kết quả tự kiểm tra selector khi mở UI Suno (open-browser + self-check).
export interface SunoSelectorGroup {
  name:    string;
  status:  'ok' | 'healed' | 'missing' | 'skipped' | 'conditional';
  matched: string | null;
  kind:    'page' | 'conditional' | 'interactive';
}

export interface SunoSelectorCheck {
  groups:           SunoSelectorGroup[];
  discovered:       Record<string, string[]>;
  summary: {
    page_ok:    number;
    page_total: number;
    healed:     string[];
    missing:    string[];
  };
  checked_at:        string;
  logged_in?:        boolean;
  saved_overrides?:  string[];
  save_error?:       string;
}

export interface SunoStatusResponse {
  batch:           SunoBatchStatus | null;
  job:             SunoJobInfo | null;
  selector_check?: SunoSelectorCheck | null;
}

// Body override khi bấm Start/Dry-run — mọi trường tuỳ chọn (bỏ trống = mặc định).
export interface SunoStartBody {
  preset?:                         string;
  // Styles/Exclusions (AI viết từ ý tưởng hoặc người dùng tự sửa). Bỏ trống = preset.
  styles?:                         string;
  exclusions?:                     string;
  suno_idea?:                      string;
  target_tracks?:                  number;
  preferred_model?:                string;
  allow_model_fallback?:           boolean;
  max_mode?:                       boolean;
  preferred_duration_seconds_min?: number;
  preferred_duration_seconds_max?: number;
  minimum_duration_seconds?:       number;
  max_create_actions?:             number;
  max_generation_credits?:         number;
  max_new_song_downloads?:         number;
  auto_continue_workflow?:         boolean;
}

// Kết quả AI viết Styles từ ý tưởng (POST /suno/generate-styles).
export interface SunoGenerateStylesBody {
  idea:    string;
  preset?: string;
  save?:   boolean;
}

export interface SunoChannelMatch {
  matched:      boolean;
  instrument:   string;
  music_style:  string;
  channel_name: string;
  confidence:   string;
  warning:      string | null;
}

export interface SunoGenerateStylesResponse {
  source:     'ai' | 'fallback';
  styles:     string;
  exclusions: string;
  note:       string | null;
  channel:    SunoChannelMatch;
}

// ── Rà soát track lẻ + tạo lại Suno + vá mix ──
export interface QcIssue {
  code: string;
  level: 'error' | 'warning';
  message: string;
}

export type QcStatus = 'ok' | 'warning' | 'error' | 'unknown';

export interface QcTrack {
  track_id: number;
  filename: string;
  duration: number;
  status: QcStatus;
  issues: QcIssue[];
  manual_flag: boolean;
  is_suno: boolean;
  request_id: string | null;
  free_spares: number;
}

export interface QcSegment {
  index: number;
  name: string;
  start: number;
  end: number;
  source_file: string;
  track_id: number | null;
  track_filename: string | null;
  status: QcStatus | 'replaced' | 'missing';
  issues: string[];
}

export interface QcTimeline {
  mix_id: number;
  mix_title: string;
  total_seconds: number;
  crossfade_seconds: number;
  segments: QcSegment[];
  outdated_segments: number;
}

export interface QcJob {
  kind: 'suno-fix' | 'remux';
  status: string;
  message: string;
  percent: number;
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface QcReplaced {
  old_track_id: number;
  new_track_id: number;
  old_filename: string;
  new_filename: string;
  source: 'spare' | 'create';
  creates: number;
  old_duration: number;
  new_duration: number;
  at: string;
}

export interface QcResponse {
  analyzed_at: string | null;
  tracks: QcTrack[];
  replaced: QcReplaced[];
  timeline: QcTimeline | null;
  job: QcJob | null;
  video_busy: boolean;
  mix_busy: boolean;
  credits_per_create: number;
  auto_regen: boolean;
  auto_fix: QcAutoFix | null;
  auto_active: boolean;
  auto_limits: { max_tracks: number; max_creates_per_track: number };
}

/** Lượt TỰ tạo lại Suno sau khi rà soát (1 lượt / bản mix). */
export interface QcAutoFix {
  mix_id: number;
  status: 'off' | 'skipped' | 'running' | 'patching' | 'done' | 'failed' | 'cancelled';
  message: string;
  track_ids?: number[];
  replaced?: number;
  failed?: number;
  at: string;
}
