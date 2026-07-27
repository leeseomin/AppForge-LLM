export type OAuthMethod = "browser" | "device-code"

export interface PkceCodes {
  verifier: string
  challenge: string
}

export interface TokenResponse {
  id_token?: string
  access_token: string
  refresh_token: string
  expires_in?: number
  token_type?: string
  scope?: string
}

export interface OAuthCredential {
  type: "oauth"
  refresh: string
  access: string
  expires: number
  accountId?: string
  metadata?: Record<string, string>
}

export interface DeviceCodeResponse {
  device_code: string
  user_code: string
  verification_uri: string
  verification_uri_complete?: string
  expires_in?: number
  interval?: number
}

export interface OAuthStartResult {
  pollId: string
  method: OAuthMethod
  url: string
  instructions: string
}

export type OAuthPollStatus = "pending" | "success" | "failed"

export interface OAuthPollResult {
  status: OAuthPollStatus
  provider?: string
  error?: string
  credential?: OAuthCredential
}

export interface OAuthProviderDescriptor {
  id: string
  name: string
  methods: Array<{ id: OAuthMethod; label: string }>
}

export type OAuthFlow = {
  providerId: string
  method: OAuthMethod
  start: () => Promise<OAuthStartResult>
  poll: (pollId: string) => Promise<OAuthPollResult>
}
