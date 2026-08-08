export type JobStatus = 'queued' | 'initializing' | 'running' | 'packaging' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled';
export type RunMode = 'autonomous' | 'checkpoint';
export type StageStatus = 'pending' | 'running' | 'validating' | 'retrying' | 'awaiting_approval' | 'completed' | 'failed';

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
  running_job_id?: string | null;
  queue_depth?: number;
  network_enabled: boolean;
  prompt_max_chars: number;
  safety: {
    deployment_enabled: boolean;
    destructive_operations_enabled: boolean;
    dependency_install_enabled: boolean;
  };
}

export interface CompactError {
  code?: string;
  title?: string;
  message?: string;
  action?: string;
  stage_label?: string | null;
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
  produces?: string[];
  artifacts?: string[];
  approval?: boolean;
  approval_required?: boolean;
  usage?: TokenUsage;
}

export interface JobEvent {
  id?: number;
  event: string;
  message: string;
  timestamp: string;
  data?: Record<string, unknown>;
}

export interface TokenUsage {
  input_tokens?: number;
  output_tokens?: number;
  non_cached_input_tokens?: number;
  cache_read_input_tokens?: number;
  cache_write_input_tokens?: number;
  reasoning_tokens?: number;
  total_tokens?: number;
  estimated_cost_usd?: number;
}

export interface ModelPricing {
  input?: number;
  output?: number;
  cache_read?: number;
  cache_write?: number;
}

export interface GenerationSettings {
  temperature?: number;
  topP?: number;
  maxTokens?: number;
}

export interface JobLlmSettings {
  provider: string | null;
  model: string | null;
  generation: GenerationSettings;
  pricing: ModelPricing;
}

export type JobRunSettings = JobLlmSettings;


export interface RoutingInfo {
  source?: string;
  pipeline?: string;
  confidence?: number | null;
  complexity?: string | null;
  rationale?: string;
  fallback_pipeline?: string;
  candidates?: unknown[];
  scores?: Record<string, number>;
}

export interface ArtifactSummary {
  name: string;
  path?: string;
  size_bytes?: number | null;
  updated_at?: string | null;
  summary?: string;
}

export interface ArtifactListPayload {
  artifacts: ArtifactSummary[];
}

export interface PreviewState {
  available: boolean;
  url: string | null;
  path?: string | null;
  built_at?: string | null;
}

export interface WorkspaceTreeEntry {
  path: string;
  type: 'file' | 'directory' | string;
  size?: number | null;
}

export interface WorkspaceTreePayload {
  root: string;
  entries: WorkspaceTreeEntry[];
  truncated: boolean;
}

export interface WorkspaceFilePayload {
  path: string;
  content: string;
  size: number;
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
  stage_label?: string | null;
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
  routing?: RoutingInfo | null;
  mode?: RunMode | string;
  queue_position?: number | null;
  parent_job_id?: string | null;
  children?: string[];
  revision_index?: number | null;
  revision_request?: string | null;
  driver: Record<string, unknown> | string | null;
  project_name: string | null;
  project_path: string | null;
  active_stage: string | null;
  stages: JobStage[];
  events: JobEvent[];
  error: ApiErrorPayload | null;
  download: DownloadState;
  preview?: PreviewState | null;
  progress: number;
  terminal: boolean;
  starred?: boolean;
  archived?: boolean;
  llm?: JobLlmSettings;
  usage?: TokenUsage;
}

export interface JobSummary {
  id: string;
  prompt: string;
  status: JobStatus | string;
  status_label: string;
  message: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  pipeline: string | null;
  mode?: RunMode | string;
  project_name?: string | null;
  parent_job_id?: string | null;
  revision_index?: number | null;
  starred: boolean;
  archived: boolean;
  llm?: JobLlmSettings;
  usage?: TokenUsage;
  progress: number;
  terminal: boolean;
}

export interface JobListPayload {
  jobs: JobSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ToastMessage {
  id: number;
  message: string;
}

export type ProviderKind = 'api-key' | 'openai-compatible';

export interface ProviderModel {
  id: string;
  name?: string;
  cost?: ModelPricing;
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
  key_source: 'stored' | 'env' | 'oauth' | 'none';
  configured: boolean;
  models?: ProviderModel[];
  model_count?: number;
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

export interface QuickConnectResult {
  ok: boolean;
  step: 'save' | 'test' | 'activate' | 'done';
  error?: string;
  provider: string;
  model?: string | null;
  test?: TestResult;
}

export interface OAuthMethod {
  id: 'browser' | 'device-code';
  label: string;
}

export interface OAuthProvider {
  id: string;
  name: string;
  methods: OAuthMethod[];
}

export interface OAuthProvidersPayload {
  providers: OAuthProvider[];
}

export interface OAuthStartResult {
  pollId: string;
  method: string;
  url: string;
  instructions: string;
}

export interface OAuthPollResult {
  status: 'pending' | 'success' | 'failed';
  provider?: string;
  error?: string;
  accountId?: string;
  expires?: number;
}

export interface OAuthRefreshResult {
  ok: true;
  provider: string;
  accountId?: string;
  expires: number;
}
