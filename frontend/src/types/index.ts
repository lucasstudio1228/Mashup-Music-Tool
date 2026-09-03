export interface Project {
  id: number;
  name: string;
  description: string;
  created_at: string;
  track_count?: number;
  mix_count?: number;
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

export type MixStatus = 'pending' | 'running' | 'completed' | 'failed';

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

export interface TrackStemInfo {
  track_stem_id?:     number;
  status:             StemStatus;
  error_message?:     string;
  volumes:            StemVolumes;
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
