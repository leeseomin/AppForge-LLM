export type ProviderKind = "api-key" | "openai-compatible"

export interface ProviderModel {
  id: string
  name?: string
}

export interface OAuthCredential {
  type: "oauth"
  refresh: string
  access: string
  expires: number
  accountId?: string
  metadata?: Record<string, string>
}

export interface ProviderDescriptor {
  id: string
  name: string
  kind: ProviderKind
  env_key?: string
  base_url_required?: boolean
  base_url_default?: string
  docs_url?: string
  default_model?: string
  models: ProviderModel[]
}

export interface ProviderConfigInput {
  apiKey?: string | null
  baseURL?: string | null
  defaultModel?: string | null
}

export interface StoredProviderConfig {
  apiKey?: string
  baseURL?: string
  defaultModel?: string
  oauth?: OAuthCredential
}

export interface ProviderStatus {
  id: string
  name: string
  kind: ProviderKind
  env_key?: string
  base_url?: string | null
  base_url_required?: boolean
  base_url_default?: string
  docs_url?: string
  has_key: boolean
  key_source: "stored" | "env" | "oauth" | "none"
  default_model?: string | null
  configured: boolean
  models?: ProviderModel[]
  model_count?: number
  oauth?: boolean
  oauth_account_id?: string
}

export interface ActiveSelection {
  provider: string | null
  model: string | null
}

export interface GenerationOptions {
  maxTokens?: number
  temperature?: number
  topP?: number
  topK?: number
  seed?: number
  stop?: string[]
}

export interface GenerateRequest {
  provider?: string
  model?: string
  system?: string
  prompt: string
  generation?: GenerationOptions
}

export interface GenerateResponse {
  provider: string
  model: string
  text: string
  finishReason: string
  usage: Record<string, unknown>
}

export interface TestRequest {
  apiKey?: string
  baseURL?: string
  model?: string
}

export interface TestResponse {
  ok: boolean
  text?: string
  error?: string
  provider?: string
  model?: string
}

export interface ApiError {
  code: string
  message: string
}
