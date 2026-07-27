import type {
  ActiveSelection,
  ApiErrorPayload,
  ArtifactListPayload,
  HealthPayload,
  JobPayload,
  PreviewState,
  RunMode,
  WorkspaceFilePayload,
  WorkspaceTreePayload,
  OAuthProvidersPayload,
  OAuthStartResult,
  OAuthPollResult,
  OAuthRefreshResult,
  ProviderModelsPayload,
  ProvidersPayload,
  QuickConnectResult,
  TestResult,
} from './types';

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

const TOKEN_KEY = 'appforge.sessionToken';

export function bootstrapSessionToken(): void {
  const url = new URL(window.location.href);
  const token = url.searchParams.get('token');
  if (!token) return;
  sessionStorage.setItem(TOKEN_KEY, token);
  url.searchParams.delete('token');
  window.history.replaceState({}, document.title, url.toString());
}

export function getSessionToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getSessionToken();
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { 'X-AppForge-Token': token } : {}),
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

export function createJob(
  prompt: string,
  options: { mode?: RunMode | string; pipeline?: string | null } = {},
): Promise<JobPayload> {
  return request<JobPayload>('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({ prompt, mode: options.mode, pipeline: options.pipeline || undefined }),
  });
}

export function getJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
}

export function reviseJob(
  jobId: string,
  requestText: string,
  options: { mode?: RunMode | string; pipeline?: string | null } = {},
): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/revise`, {
    method: 'POST',
    body: JSON.stringify({ request: requestText, mode: options.mode, pipeline: options.pipeline || undefined }),
  });
}

export function approveJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/approve`, { method: 'POST' });
}

export function retryJob(jobId: string, stage?: string | null): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: 'POST',
    body: JSON.stringify({ stage: stage || undefined }),
  });
}

export function endSession(): Promise<{ closing: boolean; message: string }> {
  return request<{ closing: boolean; message: string }>('/api/session/end', { method: 'POST' });
}

export function getProviders(): Promise<ProvidersPayload> {
  return request<ProvidersPayload>('/api/llm/providers');
}

export function getProviderModels(providerId: string): Promise<ProviderModelsPayload> {
  return request<ProviderModelsPayload>(`/api/llm/providers/${encodeURIComponent(providerId)}/models`);
}

export function saveProvider(
  providerId: string,
  body: { apiKey?: string; baseURL?: string; defaultModel?: string },
): Promise<{ status: ProvidersPayload['providers'][number] }> {
  return request(`/api/llm/providers/${encodeURIComponent(providerId)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function deleteProvider(providerId: string): Promise<{ ok: boolean }> {
  return request(`/api/llm/providers/${encodeURIComponent(providerId)}`, { method: 'DELETE' });
}

export function testProvider(
  providerId: string,
  body: { apiKey?: string; baseURL?: string; model?: string },
): Promise<TestResult> {
  return request(`/api/llm/providers/${encodeURIComponent(providerId)}/test`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getActiveProvider(): Promise<ActiveSelection> {
  return request<ActiveSelection>('/api/llm/active');
}

export function setActiveProvider(provider: string | null, model: string | null): Promise<ActiveSelection> {
  return request('/api/llm/active', {
    method: 'PUT',
    body: JSON.stringify({ provider, model }),
  });
}

export function quickConnect(body: {
  provider: string;
  apiKey: string;
  baseURL?: string;
  model?: string;
}): Promise<QuickConnectResult> {
  return request<QuickConnectResult>('/api/llm/quick-connect', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getOAuthProviders(): Promise<OAuthProvidersPayload> {
  return request<OAuthProvidersPayload>('/api/llm/oauth/providers');
}

export function startOAuth(body: {
  provider: string;
  method: string;
  enterpriseDomain?: string;
}): Promise<OAuthStartResult> {
  return request<OAuthStartResult>('/api/llm/oauth/start', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function pollOAuth(provider: string, pollId: string): Promise<OAuthPollResult> {
  return request<OAuthPollResult>(`/api/llm/oauth/poll/${encodeURIComponent(provider)}/${encodeURIComponent(pollId)}`);
}

export function refreshOAuth(provider: string): Promise<OAuthRefreshResult> {
  return request<OAuthRefreshResult>(`/api/llm/oauth/refresh/${encodeURIComponent(provider)}`, { method: 'POST' });
}


export function buildPreview(jobId: string): Promise<PreviewState> {
  return request<PreviewState>(`/api/jobs/${encodeURIComponent(jobId)}/preview/build`, { method: 'POST' });
}

export function getWorkspaceTree(jobId: string): Promise<WorkspaceTreePayload> {
  return request<WorkspaceTreePayload>(`/api/jobs/${encodeURIComponent(jobId)}/workspace/tree`);
}

export function getWorkspaceFile(jobId: string, path: string): Promise<WorkspaceFilePayload> {
  return request<WorkspaceFilePayload>(
    `/api/jobs/${encodeURIComponent(jobId)}/workspace/file?path=${encodeURIComponent(path)}`,
  );
}

export function listArtifacts(jobId: string): Promise<ArtifactListPayload> {
  return request<ArtifactListPayload>(`/api/jobs/${encodeURIComponent(jobId)}/artifacts`);
}

export function getArtifact(jobId: string, name: string): Promise<{ name: string; path: string; payload: unknown }> {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(name)}`);
}
