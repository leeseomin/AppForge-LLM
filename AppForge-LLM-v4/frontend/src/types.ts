export type JobStatus = 'queued' | 'initializing' | 'running' | 'packaging' | 'completed' | 'failed';
export type StageStatus = 'pending' | 'running' | 'validating' | 'retrying' | 'completed' | 'failed';

export interface DriverReadiness {
  ready: boolean;
  requested: string;
  selected: string | null;
  label: string;
  message: string;
  action: string;
}

export interface HealthPayload {
  status: 'ready' | 'needs_setup' | string;
  ready: boolean;
  version: string;
  driver: DriverReadiness;
  busy: boolean;
  active_job_id: string | null;
  network_enabled: boolean;
  prompt_max_chars: number;
  safety: {
    deployment_enabled: boolean;
    destructive_operations_enabled: boolean;
  };
}

export interface CompactError {
  code?: string;
  title?: string;
  message?: string;
  action?: string;
}

export interface JobStage {
  id: string;
  name: string;
  description: string;
  kind: 'system' | 'pipeline' | string;
  status: StageStatus | string;
  detail: string;
  attempt: number;
  started_at: string | null;
  completed_at: string | null;
  error: CompactError | null;
}

export interface JobEvent {
  event: string;
  message: string;
  timestamp: string;
  data?: Record<string, unknown>;
}

export interface DownloadState {
  available: boolean;
  url: string | null;
  filename: string | null;
  size_bytes: number | null;
}

export interface ApiErrorPayload {
  code: string;
  title: string;
  message: string;
  action?: string;
  stage?: string | null;
  attempt?: number | null;
  technical?: Record<string, unknown>;
  context?: Record<string, unknown>;
}

export interface JobPayload {
  id: string;
  version: string;
  prompt: string;
  status: JobStatus | string;
  status_label: string;
  message: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  pipeline: string | null;
  pipeline_description?: string | null;
  driver: Record<string, unknown> | null;
  project_name: string | null;
  project_path: string | null;
  active_stage: string | null;
  stages: JobStage[];
  events: JobEvent[];
  error: ApiErrorPayload | null;
  download: DownloadState;
  progress: number;
  terminal: boolean;
}

export interface ToastMessage {
  id: number;
  message: string;
}

export type ProviderKind = 'api-key' | 'openai-compatible';

export interface ProviderModel {
  id: string;
  name?: string;
}

export interface ProviderStatus {
  id: string;
  name: string;
  kind: ProviderKind;
  env_key?: string;
  base_url?: string | null;
  base_url_required?: boolean;
  base_url_default?: string | null;
  docs_url?: string | null;
  default_model?: string | null;
  has_key: boolean;
  key_source: 'stored' | 'env' | 'none';
  configured: boolean;
  models: ProviderModel[];
}

export interface ProvidersPayload {
  providers: ProviderStatus[];
}

export interface ActiveSelection {
  provider: string | null;
  model: string | null;
}

export interface ProviderModelsPayload {
  id: string;
  name: string;
  models: ProviderModel[];
}

export interface TestResult {
  ok: boolean;
  text?: string;
  error?: string;
  provider?: string;
  model?: string;
}
