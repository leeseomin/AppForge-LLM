import { setTimeout as sleep } from "node:timers/promises"
import { generatePKCE, generateState } from "./pkce"
import { CallbackServer } from "./callback-server"
import type { DeviceCodeResponse, OAuthCredential, OAuthPollResult, OAuthStartResult, TokenResponse } from "./types"

const CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
const AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
const TOKEN_URL = "https://auth.x.ai/oauth2/token"
const DEVICE_AUTHORIZATION_URL = "https://auth.x.ai/oauth2/device/code"
const DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
const SCOPE = "openid profile email offline_access grok-cli:access api:access"
const OAUTH_HOST = "127.0.0.1"
const OAUTH_PORT = 56121
const REDIRECT_URI = `http://${OAUTH_HOST}:${OAUTH_PORT}/callback`
const DEVICE_CODE_DEFAULT_INTERVAL_MS = 5000
const DEVICE_CODE_MIN_INTERVAL_MS = 1000
const DEVICE_CODE_SLOW_DOWN_INCREMENT_MS = 5000
const DEVICE_CODE_DEFAULT_EXPIRES_MS = 5 * 60 * 1000
const OAUTH_POLLING_SAFETY_MARGIN_MS = 3000

function authHeaders() {
  return {
    "Content-Type": "application/x-www-form-urlencoded",
    Accept: "application/json",
  }
}

function buildAuthorizeUrl(pkce: { challenge: string }, state: string, nonce: string): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    scope: SCOPE,
    code_challenge: pkce.challenge,
    code_challenge_method: "S256",
    state,
    nonce,
    plan: "generic",
    referrer: "appforge",
  })
  return `${AUTHORIZE_URL}?${params.toString()}`
}

async function exchangeCodeForTokens(code: string, pkce: { verifier: string }): Promise<TokenResponse> {
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: authHeaders(),
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: REDIRECT_URI,
      client_id: CLIENT_ID,
      code_verifier: pkce.verifier,
    }).toString(),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`xAI token exchange failed (${response.status})${detail ? `: ${detail}` : ""}`)
  }
  return response.json() as Promise<TokenResponse>
}

function toCredential(tokens: TokenResponse): OAuthCredential {
  return {
    type: "oauth",
    refresh: tokens.refresh_token,
    access: tokens.access_token,
    expires: Date.now() + (tokens.expires_in ?? 3600) * 1000,
  }
}

export async function refreshXaiToken(refreshToken: string): Promise<OAuthCredential> {
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: authHeaders(),
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: CLIENT_ID,
    }).toString(),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`xAI token refresh failed (${response.status})${detail ? `: ${detail}` : ""}`)
  }
  return toCredential(await response.json() as TokenResponse)
}

function positiveSecondsToMs(value: unknown, defaultMs: number): number {
  const seconds = Number(value)
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : defaultMs
}

async function requestDeviceCode(): Promise<DeviceCodeResponse> {
  const response = await fetch(DEVICE_AUTHORIZATION_URL, {
    method: "POST",
    headers: authHeaders(),
    body: new URLSearchParams({ client_id: CLIENT_ID, scope: SCOPE }).toString(),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`xAI device code request failed (${response.status})${detail ? `: ${detail}` : ""}`)
  }
  const json = (await response.json()) as DeviceCodeResponse
  if (!json.device_code || !json.user_code || !json.verification_uri) {
    throw new Error("xAI device code response missing required fields")
  }
  return json
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
  const pkce = await generatePKCE(64)
  const state = generateState()
  const nonce = generateState()
  const callbackServer = new CallbackServer()
  await callbackServer.start(OAUTH_PORT, OAUTH_HOST)
  const authUrl = buildAuthorizeUrl(pkce, state, nonce)
  const pollId = crypto.randomUUID()
  const flow: ActiveFlow = { pkce, state, callbackServer, result: null, done: false }
  flows.set(pollId, flow)

  callbackServer
    .waitForCallback(pkce, state)
    .then(async (code) => {
      try {
        const tokens = await exchangeCodeForTokens(code, pkce)
        flow.result = { status: "success", provider: "xai", credential: toCredential(tokens) }
      } catch (err) {
        flow.result = { status: "failed", provider: "xai", error: err instanceof Error ? err.message : String(err) }
      } finally {
        flow.done = true
        callbackServer.stop()
      }
    })
    .catch((err) => {
      flow.result = { status: "failed", provider: "xai", error: err instanceof Error ? err.message : String(err) }
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

interface DeviceFlowState {
  device: DeviceCodeResponse
  result: OAuthPollResult | null
  done: boolean
}

const deviceFlows = new Map<string, DeviceFlowState>()

export async function startDeviceFlow(): Promise<OAuthStartResult> {
  const device = await requestDeviceCode()
  const pollId = crypto.randomUUID()
  deviceFlows.set(pollId, { device, result: null, done: false })

  pollDeviceToken(pollId).catch((err) => {
    const flow = deviceFlows.get(pollId)
    if (flow) {
      flow.result = { status: "failed", provider: "xai", error: err instanceof Error ? err.message : String(err) }
      flow.done = true
    }
  })

  return {
    pollId,
    method: "device-code",
    url: device.verification_uri_complete ?? device.verification_uri,
    instructions: `Open ${device.verification_uri} on any device and enter code: ${device.user_code}`,
  }
}

async function pollDeviceToken(pollId: string): Promise<void> {
  const flow = deviceFlows.get(pollId)
  if (!flow) return
  const now = () => Date.now()
  const expiresInMs = positiveSecondsToMs(flow.device.expires_in, DEVICE_CODE_DEFAULT_EXPIRES_MS)
  const deadline = now() + expiresInMs
  let intervalMs = Math.max(positiveSecondsToMs(flow.device.interval, DEVICE_CODE_DEFAULT_INTERVAL_MS), DEVICE_CODE_MIN_INTERVAL_MS)

  while (now() < deadline) {
    const response = await fetch(TOKEN_URL, {
      method: "POST",
      headers: authHeaders(),
      body: new URLSearchParams({
        grant_type: DEVICE_CODE_GRANT_TYPE,
        client_id: CLIENT_ID,
        device_code: flow.device.device_code,
      }).toString(),
    })
    if (response.ok) {
      const tokens = await response.json() as TokenResponse
      flow.result = { status: "success", provider: "xai", credential: toCredential(tokens) }
      flow.done = true
      return
    }
    const body = (await response.json().catch(() => ({}))) as { error?: string; error_description?: string }
    const remaining = Math.max(0, deadline - now())
    if (body.error === "authorization_pending") {
      await sleep(Math.min(intervalMs + OAUTH_POLLING_SAFETY_MARGIN_MS, remaining))
      continue
    }
    if (body.error === "slow_down") {
      intervalMs += DEVICE_CODE_SLOW_DOWN_INCREMENT_MS
      await sleep(Math.min(intervalMs + OAUTH_POLLING_SAFETY_MARGIN_MS, remaining))
      continue
    }
    if (body.error === "access_denied" || body.error === "authorization_denied") {
      flow.result = { status: "failed", provider: "xai", error: "xAI device authorization was denied" }
      flow.done = true
      return
    }
    if (body.error === "expired_token") {
      flow.result = { status: "failed", provider: "xai", error: "xAI device code expired - please re-run login" }
      flow.done = true
      return
    }
    const detail = body.error_description ?? body.error ?? ""
    flow.result = { status: "failed", provider: "xai", error: `xAI device token exchange failed (${response.status})${detail ? `: ${detail}` : ""}` }
    flow.done = true
    return
  }
  flow.result = { status: "failed", provider: "xai", error: "xAI device authorization timed out" }
  flow.done = true
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

export const xaiOAuth = {
  providerId: "xai",
  methods: [
    { id: "browser" as const, label: "xAI Grok OAuth (SuperGrok Subscription)" },
    { id: "device-code" as const, label: "xAI Grok OAuth (Headless / Remote / VPS)" },
  ],
  start: (method: "browser" | "device-code", _options?: { enterpriseDomain?: string }) => (method === "browser" ? startBrowserFlow() : startDeviceFlow()),
  poll: pollFlow,
  refresh: refreshXaiToken,
}
