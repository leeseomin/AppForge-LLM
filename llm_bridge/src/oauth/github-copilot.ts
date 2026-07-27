import { setTimeout as sleep } from "node:timers/promises"
import type { OAuthCredential, OAuthPollResult, OAuthStartResult } from "./types"

const CLIENT_ID = "Ov23li8tweQw6odWQebz"
const OAUTH_POLLING_SAFETY_MARGIN_MS = 3000

function normalizeDomain(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "")
}

function getUrls(domain: string) {
  return {
    deviceCodeUrl: `https://${domain}/login/device/code`,
    accessTokenUrl: `https://${domain}/login/oauth/access_token`,
  }
}

interface DeviceFlowState {
  deviceCode: string
  interval: number
  result: OAuthPollResult | null
  done: boolean
}

const flows = new Map<string, DeviceFlowState>()

export async function startDeviceFlow(enterpriseDomain?: string): Promise<OAuthStartResult> {
  const domain = enterpriseDomain ? normalizeDomain(enterpriseDomain) : "github.com"
  const urls = getUrls(domain)
  const response = await fetch(urls.deviceCodeUrl, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ client_id: CLIENT_ID, scope: "read:user" }),
  })
  if (!response.ok) throw new Error("Failed to initiate GitHub device authorization")
  const data = (await response.json()) as {
    verification_uri: string
    user_code: string
    device_code: string
    interval: number
  }
  const pollId = crypto.randomUUID()
  flows.set(pollId, { deviceCode: data.device_code, interval: data.interval, result: null, done: false })

  pollToken(pollId, urls.accessTokenUrl, domain).catch((err) => {
    const flow = flows.get(pollId)
    if (flow) {
      flow.result = { status: "failed", provider: "github-copilot", error: err instanceof Error ? err.message : String(err) }
      flow.done = true
    }
  })

  return {
    pollId,
    method: "device-code",
    url: data.verification_uri,
    instructions: `Enter code: ${data.user_code}`,
  }
}

async function pollToken(pollId: string, accessTokenUrl: string, domain: string): Promise<void> {
  const flow = flows.get(pollId)
  if (!flow) return
  let currentInterval = flow.interval * 1000
  while (true) {
    const response = await fetch(accessTokenUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        client_id: CLIENT_ID,
        device_code: flow.deviceCode,
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      }),
    })
    if (!response.ok) {
      flow.result = { status: "failed", provider: "github-copilot", error: `GitHub token request failed: ${response.status}` }
      flow.done = true
      return
    }
    const data = (await response.json()) as {
      access_token?: string
      error?: string
      interval?: number
    }
    if (data.access_token) {
      const credential: OAuthCredential = {
        type: "oauth",
        refresh: data.access_token,
        access: data.access_token,
        expires: 0,
        metadata: domain !== "github.com" ? { enterpriseUrl: domain } : undefined,
      }
      flow.result = { status: "success", provider: "github-copilot", credential }
      flow.done = true
      return
    }
    if (data.error === "authorization_pending") {
      await sleep(currentInterval + OAUTH_POLLING_SAFETY_MARGIN_MS)
      continue
    }
    if (data.error === "slow_down") {
      currentInterval = (data.interval ?? flow.interval + 5) * 1000
      await sleep(currentInterval + OAUTH_POLLING_SAFETY_MARGIN_MS)
      continue
    }
    if (data.error) {
      flow.result = { status: "failed", provider: "github-copilot", error: `GitHub authorization failed: ${data.error}` }
      flow.done = true
      return
    }
    await sleep(currentInterval + OAUTH_POLLING_SAFETY_MARGIN_MS)
  }
}

export function pollFlow(pollId: string): OAuthPollResult {
  const flow = flows.get(pollId)
  if (flow) {
    if (flow.done && flow.result) {
      flows.delete(pollId)
      return flow.result
    }
    return { status: "pending" }
  }
  return { status: "failed", error: "Unknown pollId" }
}

export const githubCopilotOAuth = {
  providerId: "github-copilot",
  methods: [
    { id: "device-code" as const, label: "GitHub Copilot (device code)" },
  ],
  start: (_method: "device-code", options?: { enterpriseDomain?: string }) => startDeviceFlow(options?.enterpriseDomain),
  poll: pollFlow,
  refresh: async (refreshToken: string) => {
    return {
      type: "oauth" as const,
      refresh: refreshToken,
      access: refreshToken,
      expires: 0,
    }
  },
}
