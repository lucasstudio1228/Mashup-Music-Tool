import axios from 'axios';
import type { MixProgressEvent } from '../types';

export const API_BASE = 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

export function downloadUrl(mixId: number, fileType: string): string {
  return `${API_BASE}/api/mixes/${mixId}/download/${fileType}`;
}

/** SSE helper – dùng native EventSource (axios không hỗ trợ SSE). */
export function createProgressStream(
  mixId: number,
  onProgress: (event: MixProgressEvent) => void,
  onDone: () => void,
  onError: (err: Event) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}/api/mixes/${mixId}/progress`);
  es.addEventListener('progress', (e) =>
    onProgress(JSON.parse((e as MessageEvent).data)),
  );
  es.addEventListener('done', () => {
    es.close();
    onDone();
  });
  es.addEventListener('error', (e) => {
    // EventSource tự reconnect khi lỗi mạng; chỉ báo khi thực sự đóng.
    if (es.readyState === EventSource.CLOSED) onError(e);
  });
  return es;
}
