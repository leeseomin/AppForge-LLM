import { setTimeout as sleep } from "node:timers/promises"
import { generatePKCE, generateState, parseJwtClaims, base64UrlEncode } from "./pkce"
import { CallbackServer } from "./callback-server"
import type { OAuthCredential, OAuthPollResult, OAuthStartResult, TokenResponse } from "./types"

const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
const ISSUER = "https://auth.openai.com"
const OAUTH_PORT = 1455
const OAUTH_POLLING_SAFETY_MARGIN_MS = 3000

interface IdTokenClaims {
  chatgpt_account_id?: string
  organizations?: Array<{ id: string }>
  "https://api.openai.com/auth"?: { chatgpt_account_id?: string }
}

function extractAccountId(tokens: TokenResponse): string | undefined {
  for (const token of [tokens.id_token, tokens.access_token]) {
    if (!token) continue
    const claims = parseJwtClaims(token) as IdTokenClaims | undefined
    if (!claims) continue
    const id = claims.chatgpt_account_id ?? claims["https://api.openai.com/auth"]?.chatgpt_account_id ?? claims.organizations?.[0]?.id
    if (id) return id
  }
  return undefined
}

function buildAuthorizeUrl(redirectUri: string, pkce: { challenge: string }, state: string): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: redirectUri,
    scope: "openid profile email offline_access",
    code_challenge: pkce.challenge,
    code_challenge_method: "S256",
    id_token_add_organizations: "true",
    codex_cli_simplified_flow: "true",
    state,
    originator: "appforge",
  })
  return `${ISSUER}/oauth/authorize?${params.toString()}`
}

async function exchangeCodeForTokens(code: string, redirectUri: string, pkce: { verifier: string }): Promise<TokenResponse> {
  const response = await fetch(`${ISSUER}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      client_id: CLIENT_ID,
      code_verifier: pkce.verifier,
    }).toString(),
  })
  if (!response.ok) throw new Error(`Token exchange failed: ${response.status}`)
  return response.json() as Promise<TokenResponse>
}

function toCredential(tokens: TokenResponse): OAuthCredential {
  return {
    type: "oauth",
    refresh: tokens.refresh_token,
    access: tokens.access_token,
    expires: Date.now() + (tokens.expires_in ?? 3600) * 1000,
    accountId: extractAccountId(tokens),
  }
}

export async function refreshOpenAIToken(refreshToken: string): Promise<OAuthCredential> {
  const response = await fetch(`${ISSUER}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: CLIENT_ID,
    }).toString(),
  })
  if (!response.ok) throw new Error(`OpenAI token refresh failed: ${response.status}`)
  return toCredential(await response.json() as TokenResponse)
}

interface ActiveFlow {
  pkce: { verifier: string; challenge: string }
  state: string
  callbackServer: CallbackServer
  result: OAuthPollResult | null
  done: boolean
}

const flows = new Map<string, ActiveFlow>()

export async function startBrowserFlow(): Promise<OAuthStartResult> {
  const pkce = await generatePKCE(43)
  const state = base64UrlEncode(crypto.getRandomValues(new Uint8Array(32)).buffer)
  const callbackServer = new CallbackServer()
  const redirectUri = await callbackServer.start(OAUTH_PORT, "localhost")
  const authUrl = buildAuthorizeUrl(redirectUri, pkce, state)
  const pollId = crypto.randomUUID()
  const flow: ActiveFlow = { pkce, state, callbackServer, result: null, done: false }
  flows.set(pollId, flow)

  callbackServer
    .waitForCallback(pkce, state)
    .then(async (code) => {
      try {
        const tokens = await exchangeCodeForTokens(code, redirectUri, pkce)
        flow.result = { status: "success", provider: "openai", credential: toCredential(tokens) }
      } catch (err) {
        flow.result = { status: "failed", provider: "openai", error: err instanceof Error ? err.message : String(err) }
      } finally {
        flow.done = true
        callbackServer.stop()
      }
    })
    .catch((err) => {
      flow.result = { status: "failed", provider: "openai", error: err instanceof Error ? err.message : String(err) }
      flow.done = true
      callbackServer.stop()
    })

  return {
    pollId,
    method: "browser",
    url: authUrl,
    instructions: "Complete authorization in your browser. This window will close automatically.",
  }
}

interface DeviceFlow {
  deviceAuthId: string
  userCode: string
  interval: number
  result: OAuthPollResult | null
  done: boolean
}

const deviceFlows = new Map<string, DeviceFlow>()

export async function startDeviceFlow(): Promise<OAuthStartResult> {
  const response = await fetch(`${ISSUER}/api/accounts/deviceauth/usercode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: CLIENT_ID }),
  })
  if (!response.ok) throw new Error("Failed to initiate device authorization")
  const data = (await response.json()) as { device_auth_id: string; user_code: string; interval: string }
  const interval = Math.max(parseInt(data.interval) || 5, 1) * 1000
  const pollId = crypto.randomUUID()
  deviceFlows.set(pollId, { deviceAuthId: data.device_auth_id, userCode: data.user_code, interval, result: null, done: false })

  pollDeviceToken(pollId).catch((err) => {
    const flow = deviceFlows.get(pollId)
    if (flow) {
      flow.result = { status: "failed", provider: "openai", error: err instanceof Error ? err.message : String(err) }
      flow.done = true
    }
  })

  return {
    pollId,
    method: "device-code",
    url: `${ISSUER}/codex/device`,
    instructions: `Enter code: ${data.user_code}`,
  }
}

async function pollDeviceToken(pollId: string): Promise<void> {
  const flow = deviceFlows.get(pollId)
  if (!flow) return
  while (true) {
    const response = await fetch(`${ISSUER}/api/accounts/deviceauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_auth_id: flow.deviceAuthId, user_code: flow.userCode }),
    })
    if (response.ok) {
      const data = (await response.json()) as { authorization_code: string; code_verifier: string }
      const tokenResponse = await fetch(`${ISSUER}/oauth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          code: data.authorization_code,
          redirect_uri: `${ISSUER}/deviceauth/callback`,
          client_id: CLIENT_ID,
          code_verifier: data.code_verifier,
        }).toString(),
      })
      if (!tokenResponse.ok) throw new Error(`Token exchange failed: ${tokenResponse.status}`)
      const tokens = await tokenResponse.json() as TokenResponse
      flow.result = { status: "success", provider: "openai", credential: toCredential(tokens) }
      flow.done = true
      return
    }
    if (response.status !== 403 && response.status !== 404) {
      flow.result = { status: "failed", provider: "openai", error: `Device authorization failed: ${response.status}` }
      flow.done = true
      return
    }
    await sleep(flow.interval + OAUTH_POLLING_SAFETY_MARGIN_MS)
  }
}

export function pollFlow(pollId: string): OAuthPollResult {
  const browserFlow = flows.get(pollId)
  if (browserFlow) {
    if (browserFlow.done && browserFlow.result) {
      flows.delete(pollId)
      return browserFlow.result
    }
    return { status: "pending" }
  }
  const deviceFlow = deviceFlows.get(pollId)
  if (deviceFlow) {
    if (deviceFlow.done && deviceFlow.result) {
      deviceFlows.delete(pollId)
      return deviceFlow.result
    }
    return { status: "pending" }
  }
  return { status: "failed", error: "Unknown pollId" }
}

export const openaiOAuth = {
  providerId: "openai",
  methods: [
    { id: "browser" as const, label: "ChatGPT Pro/Plus (browser)" },
    { id: "device-code" as const, label: "ChatGPT Pro/Plus (headless)" },
  ],
  start: (method: "browser" | "device-code") => (method === "browser" ? startBrowserFlow() : startDeviceFlow()),
  poll: pollFlow,
  refresh: refreshOpenAIToken,
}
