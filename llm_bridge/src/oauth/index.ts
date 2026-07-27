import { openaiOAuth } from "./openai"
import { xaiOAuth } from "./xai"
import { githubCopilotOAuth } from "./github-copilot"
import type { OAuthCredential, OAuthPollResult, OAuthProviderDescriptor, OAuthStartResult } from "./types"

export * from "./types"

interface OAuthProviderHandler {
  providerId: string
  methods: Array<{ id: "browser" | "device-code"; label: string }>
  start: (
    method: "browser" | "device-code",
    options?: { enterpriseDomain?: string },
  ) => Promise<OAuthStartResult>
  poll: (pollId: string) => OAuthPollResult
  refresh: (refreshToken: string) => Promise<OAuthCredential>
}

const REGISTRY: Record<string, OAuthProviderHandler> = {
  openai: openaiOAuth as unknown as OAuthProviderHandler,
  xai: xaiOAuth as unknown as OAuthProviderHandler,
  "github-copilot": githubCopilotOAuth as unknown as OAuthProviderHandler,
}

export function listOAuthProviders(): OAuthProviderDescriptor[] {
  return Object.values(REGISTRY).map((p) => ({ id: p.providerId, name: p.providerId, methods: p.methods }))
}

export function getOAuthProvider(id: string): OAuthProviderHandler | undefined {
  return REGISTRY[id]
}

export function isOAuthProvider(id: string): boolean {
  return id in REGISTRY
}

export async function startOAuthFlow(
  providerId: string,
  method: "browser" | "device-code",
  options?: { enterpriseDomain?: string },
): Promise<OAuthStartResult> {
  const handler = REGISTRY[providerId]
  if (!handler) throw new Error(`No OAuth handler for provider '${providerId}'`)
  return handler.start(method, options)
}

export function pollOAuthFlow(providerId: string, pollId: string): OAuthPollResult {
  const handler = REGISTRY[providerId]
  if (!handler) return { status: "failed", error: `No OAuth handler for provider '${providerId}'` }
  return handler.poll(pollId)
}

export async function refreshOAuthToken(providerId: string, refreshToken: string): Promise<OAuthCredential> {
  const handler = REGISTRY[providerId]
  if (!handler) throw new Error(`No OAuth handler for provider '${providerId}'`)
  return handler.refresh(refreshToken)
}
