import type { OAuthCredential, OAuthPollResult } from "./types"

export interface PublicOAuthPollResult {
  status: OAuthPollResult["status"]
  provider?: string
  error?: string
  accountId?: string
  expires?: number
}

export interface PublicOAuthRefreshResult {
  ok: true
  provider: string
  accountId?: string
  expires: number
}

export function publicOAuthPollResult(
  result: OAuthPollResult,
  providerId: string,
): PublicOAuthPollResult {
  const output: PublicOAuthPollResult = {
    status: result.status,
    provider: providerId,
  }
  if (result.error) output.error = result.error
  if (result.status === "success" && result.credential) {
    if (result.credential.accountId) output.accountId = result.credential.accountId
    output.expires = result.credential.expires
  }
  return output
}

export function publicOAuthRefreshResult(
  providerId: string,
  credential: OAuthCredential,
): PublicOAuthRefreshResult {
  const output: PublicOAuthRefreshResult = {
    ok: true,
    provider: providerId,
    expires: credential.expires,
  }
  if (credential.accountId) output.accountId = credential.accountId
  return output
}
