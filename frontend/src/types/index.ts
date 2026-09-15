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
  clips:          string[];
  clip_count:     number;
  final_exists:   boolean;
  final_path?:    string | null;
  thumbnail_path?: string | null;
  audio_path?:    string | null;
  audio_duration?: number | null;
  job?:           VideoJobInfo | null;
  params:         VideoParamsInfo;
}
