import type { ApiErrorPayload, HealthPayload, JobPayload } from './types';

export class ApiError extends Error {
  readonly payload: ApiErrorPayload;
  readonly status: number;

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.message || payload.title || '요청 실패');
    this.name = 'ApiError';
    this.payload = payload;
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : null;

  if (!response.ok) {
    const errorPayload: ApiErrorPayload = payload?.error || {
      code: `HTTP_${response.status}`,
      title: '요청을 처리하지 못했습니다',
      message: `서버가 HTTP ${response.status} 응답을 반환했습니다.`,
      action: '서버 상태를 확인한 뒤 다시 시도하세요.',
      context: {},
    };
    throw new ApiError(errorPayload, response.status);
  }

  return payload as T;
}

export function getHealth(): Promise<HealthPayload> {
  return request<HealthPayload>('/api/health');
}

export function createJob(prompt: string): Promise<JobPayload> {
  return request<JobPayload>('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({ prompt }),
  });
}

export function getJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}`);
}
