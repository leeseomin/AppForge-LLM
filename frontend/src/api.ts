import type {
  ActiveSelection,
  ApiErrorPayload,
  ArtifactListPayload,
  HealthPayload,
  JobPayload,
  JobListPayload,
  JobRunSettings,
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
import { translateCurrent } from './i18n';

export class ApiError extends Error {
  readonly payload: ApiErrorPayload;
  readonly status: number;

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.message || payload.title || translateCurrent('api.requestFailed'));
    this.name = 'ApiError';
    this.payload = payload;
    this.status = status;
  }
}

export async function bootstrapSession(): Promise<void> {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.replace(/^#/, ''));
  const code = fragment.get('bootstrap');
  if (!code) return;
  url.hash = '';
  window.history.replaceState({}, document.title, url.toString());
  const response = await fetch('/api/session/bootstrap', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
  if (!response.ok) {
    throw new Error(translateCurrent('api.requestFailed'));
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
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
      title: translateCurrent('api.requestTitle'),
      message: translateCurrent('api.httpError', { status: response.status }),
      action: translateCurrent('api.checkServer'),
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
  options: {
    mode?: RunMode | string;
    pipeline?: string | null;
    provider?: string | null;
    model?: string | null;
    generation?: JobRunSettings['generation'];
    pricing?: JobRunSettings['pricing'];
  } = {},
): Promise<JobPayload> {
  return request<JobPayload>('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({
      prompt,
      mode: options.mode,
      pipeline: options.pipeline || undefined,
      provider: options.provider || undefined,
      model: options.model || undefined,
      generation: options.generation,
      pricing: options.pricing,
    }),
  });
}

export function listJobs(
  options: { limit?: number; cursor?: string | null; archived?: boolean } = {},
): Promise<JobListPayload> {
  const params = new URLSearchParams();
  params.set('limit', String(options.limit ?? 20));
  if (options.cursor) params.set('cursor', options.cursor);
  if (options.archived) params.set('archived', 'true');
  return request<JobListPayload>(`/api/jobs?${params.toString()}`);
}

export function updateJobMetadata(
  jobId: string,
  updates: { starred?: boolean; archived?: boolean },
): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
}

export function rerunJob(jobId: string): Promise<JobPayload> {
  return request<JobPayload>(`/api/jobs/${encodeURIComponent(jobId)}/rerun`, { method: 'POST' });
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
  body: { apiKey?: string; clearApiKey?: boolean; baseURL?: string; defaultModel?: string },
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
