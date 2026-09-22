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

export type MixStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

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
  image_count:    number;
  clip_seconds:   number;
  rest_clips:     number;
  total_clips:    number;
  t_window:       number;
  model:          string;
  aspect_ratio:   string;
  flow_mode:      string;
  blend_seconds:  number;
  slow_speed:     number;
}

export type VideoJobStatus = 'pending' | 'running' | 'completed' | 'failed';

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
