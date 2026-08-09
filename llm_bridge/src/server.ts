import { createHash, timingSafeEqual } from "node:crypto"
import * as registry from "./registry"
import * as store from "./config"
import * as catalog from "./catalog"
import * as oauth from "./oauth"
import { publicOAuthPollResult, publicOAuthRefreshResult } from "./oauth/public"
import { BridgeLLMCancelled, BridgeLLMError, generate, runOnce, stream, type StreamEvent } from "./llm"
import { VERSION } from "./version"
import type {
  ActiveSelection,
  AgentToolDefinition,
  ChatMessageInput,
  GenerateRequest,
  GenerationOptions,
  OAuthCredential,
  ProviderStatus,
  TestRequest,
  TestResponse,
} from "./types"

const PORT = Number(process.env.PORT || process.env.APPFORGE_LLM_BRIDGE_PORT || 8788)
const HOST = process.env.HOST || process.env.APPFORGE_LLM_BRIDGE_HOST || "127.0.0.1"
const DEFAULT_IDLE_TIMEOUT_SECONDS = 30
const DEFAULT_SSE_HEARTBEAT_MS = 5_000
const BRIDGE_TOKEN_HEADER = "x-appforge-bridge-token"
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"])

function heartbeatIntervalMs(): number {
  const raw = process.env.APPFORGE_LLM_BRIDGE_HEARTBEAT_MS
  if (raw === undefined || raw.trim() === "") return DEFAULT_SSE_HEARTBEAT_MS
  const parsed = Number(raw)
  if (!Number.isFinite(parsed)) return DEFAULT_SSE_HEARTBEAT_MS
  return Math.min(60_000, Math.max(1_000, Math.floor(parsed)))
}

const SSE_HEARTBEAT_MS = heartbeatIntervalMs()

function idleTimeoutSeconds(): number {
  const raw = process.env.APPFORGE_LLM_BRIDGE_IDLE_TIMEOUT
  if (raw === undefined || raw.trim() === "") return DEFAULT_IDLE_TIMEOUT_SECONDS
  const parsed = Number(raw)
  if (!Number.isFinite(parsed)) return DEFAULT_IDLE_TIMEOUT_SECONDS
  return Math.min(255, Math.max(0, Math.floor(parsed)))
}

type Handler = (request: Request, match: RegExpMatchArray) => Promise<Response> | Response

interface Route {
  method: string
  pattern: RegExp
  handler: Handler
}

const ROUTES: Route[] = [
  { method: "GET", pattern: /^\/health\/?$/, handler: health },
  { method: "GET", pattern: /^\/ready\/?$/, handler: ready },
  { method: "GET", pattern: /^\/providers\/?$/, handler: listProviders },
  { method: "POST", pattern: /^\/catalog\/refresh\/?$/, handler: refreshCatalog },
  { method: "PUT", pattern: /^\/providers\/([^/]+)\/?$/, handler: upsertProvider },
  { method: "DELETE", pattern: /^\/providers\/([^/]+)\/?$/, handler: removeProvider },
  { method: "GET", pattern: /^\/providers\/([^/]+)\/models\/?$/, handler: providerModels },
  { method: "POST", pattern: /^\/providers\/([^/]+)\/test\/?$/, handler: testProvider },
  { method: "GET", pattern: /^\/active\/?$/, handler: getActive },
  { method: "PUT", pattern: /^\/active\/?$/, handler: setActive },
  { method: "POST", pattern: /^\/generate\/?$/, handler: generateHandler },
  { method: "POST", pattern: /^\/stream\/?$/, handler: streamHandler },
  { method: "POST", pattern: /^\/agent\/start\/?$/, handler: agentStart },
  { method: "GET", pattern: /^\/agent\/([^/]+)\/events\/?$/, handler: agentEvents },
  { method: "POST", pattern: /^\/agent\/([^/]+)\/tool_result\/?$/, handler: agentToolResult },
  { method: "DELETE", pattern: /^\/agent\/([^/]+)\/?$/, handler: agentDelete },
  { method: "GET", pattern: /^\/oauth\/providers\/?$/, handler: oauthProviders },
  { method: "POST", pattern: /^\/oauth\/start\/?$/, handler: oauthStart },
  { method: "GET", pattern: /^\/oauth\/poll\/([^/]+)\/([^/]+)\/?$/, handler: oauthPoll },
  { method: "POST", pattern: /^\/oauth\/refresh\/([^/]+)\/?$/, handler: oauthRefresh },
]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  })
}

function errorResponse(message: string, status = 400, code = "BRIDGE_ERROR"): Response {
  return json({ error: { code, message } }, status)
}

function redactSensitiveError(message: string): string {
  return message
    .replace(/\b(?:sk-(?:ant-|or-v1-|proj-|svcacct-)?|xai-)[A-Za-z0-9_-]{12,}\b/gi, "[REDACTED]")
    .replace(/\bAIza[0-9A-Za-z_-]{20,}\b/g, "[REDACTED]")
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [REDACTED]")
    .replace(/:\/\/[^/@\s]+:[^/@\s]+@/g, "://[REDACTED]@")
    .slice(0, 500)
}

function publicErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof registry.BridgeRegistryError) {
    return redactSensitiveError(error.message)
  }
  if (error instanceof BridgeLLMError) {
    return redactSensitiveError(error.message || fallback)
  }
  return fallback
}

function cors(response: Response): Response {
  response.headers.set("cache-control", "no-store")
  response.headers.set("x-content-type-options", "nosniff")
  response.headers.set("referrer-policy", "no-referrer")
  return response
}

async function readJsonBody(request: Request): Promise<Record<string, unknown>> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase()
  if (contentType !== "application/json") {
    throw new BridgeHttpError(
      "Requests with a body must use application/json.",
      415,
      "JSON_CONTENT_TYPE_REQUIRED",
    )
  }
  if (!request.body) return {}
  const text = await request.text()
  if (text.length > 1_000_000) {
    throw new BridgeHttpError("JSON body is too large.", 413, "REQUEST_BODY_TOO_LARGE")
  }
  if (!text) return {}
  try {
    const parsed = JSON.parse(text)
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {}
  } catch {
    throw new BridgeHttpError("Invalid JSON body", 400, "INVALID_JSON")
  }
}

export class BridgeHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message)
    this.name = "BridgeHttpError"
  }
}

interface AgentToolResultEnvelope {
  result: unknown
  is_error: boolean
}

interface PendingToolResult {
  name: string
  resolve: (value: AgentToolResultEnvelope) => void
  reject: (error: Error) => void
  timeout: ReturnType<typeof setTimeout>
}

interface AgentSession {
  id: string
  createdAt: number
  lastActivityAt: number
  system?: string
  provider?: string
  model?: string
  generation?: GenerationOptions
  responseFormat?: Record<string, unknown>
  tools: AgentToolDefinition[]
  messages: ChatMessageInput[]
  pending: Map<string, PendingToolResult>
  announcedToolCalls: Map<string, string>
  queuedToolResults: Map<string, AgentToolResultEnvelope>
  completedToolResults: Map<string, AgentToolResultEnvelope>
  eventStreamActive: boolean
  closed: boolean
}

const AGENT_SESSION_TTL_MS = 30 * 60 * 1000
const AGENT_MAX_TURNS = 80
const agentSessions = new Map<string, AgentSession>()
let agentStreamImplementation: typeof stream = stream

export function _setAgentStreamForTest(value: typeof stream | null): void {
  agentStreamImplementation = value ?? stream
}

function randomSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID()
  return `agent_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}

function touchAgentSession(session: AgentSession): void {
  session.lastActivityAt = Date.now()
}

function getAgentSession(id: string): AgentSession | undefined {
  const session = agentSessions.get(id)
  if (!session) return undefined
  if (session.closed || Date.now() - session.lastActivityAt > AGENT_SESSION_TTL_MS) {
    closeAgentSession(session)
    return undefined
  }
  touchAgentSession(session)
  return session
}

function closeAgentSession(session: AgentSession): void {
  session.closed = true
  agentSessions.delete(session.id)
  for (const pending of session.pending.values()) {
    clearTimeout(pending.timeout)
    pending.reject(new Error("Agent session was closed before the tool result arrived."))
  }
  session.pending.clear()
  session.announcedToolCalls.clear()
  session.queuedToolResults.clear()
  session.completedToolResults.clear()
}

function waitForToolResult(session: AgentSession, callId: string, name: string, signal: AbortSignal): Promise<AgentToolResultEnvelope> {
  if (signal.aborted) return Promise.reject(new BridgeLLMCancelled())
  touchAgentSession(session)
  const queued = session.queuedToolResults.get(callId)
  if (queued) {
    session.queuedToolResults.delete(callId)
    session.announcedToolCalls.delete(callId)
    rememberCompletedToolResult(session, callId, queued)
    return Promise.resolve(queued)
  }
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      signal.removeEventListener("abort", onAbort)
      session.pending.delete(callId)
      clearTimeout(timeout)
    }
    const onAbort = () => {
      cleanup()
      reject(new BridgeLLMCancelled())
    }
    const timeout = setTimeout(() => {
      cleanup()
      reject(new Error(`Timed out waiting for tool result '${name}' (${callId}).`))
    }, AGENT_SESSION_TTL_MS)
    session.pending.set(callId, {
      name,
      resolve: (value) => {
        cleanup()
        session.announcedToolCalls.delete(callId)
        rememberCompletedToolResult(session, callId, value)
        touchAgentSession(session)
        resolve(value)
      },
      reject: (error) => {
        cleanup()
        reject(error)
      },
      timeout,
    })
    signal.addEventListener("abort", onAbort, { once: true })
  })
}

function toolResultKey(value: AgentToolResultEnvelope): string {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value.result)
  }
}

function sameToolResult(left: AgentToolResultEnvelope, right: AgentToolResultEnvelope): boolean {
  return toolResultKey(left) === toolResultKey(right)
}

function rememberCompletedToolResult(session: AgentSession, callId: string, value: AgentToolResultEnvelope): void {
  session.completedToolResults.set(callId, value)
  while (session.completedToolResults.size > 256) {
    const oldest = session.completedToolResults.keys().next().value
    if (typeof oldest !== "string") break
    session.completedToolResults.delete(oldest)
  }
}

function sseHeaders(): Headers {
  return new Headers({
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  })
}

function asToolDefinitions(value: unknown): AgentToolDefinition[] {
  if (!Array.isArray(value)) return []
  const out: AgentToolDefinition[] = []
  for (const item of value) {
    if (!item || typeof item !== "object") continue
    const raw = item as Record<string, unknown>
    if (typeof raw.name !== "string" || !raw.name) continue
    out.push({
      name: raw.name,
      description: typeof raw.description === "string" ? raw.description : raw.name,
      parameters: raw.parameters && typeof raw.parameters === "object" ? (raw.parameters as Record<string, unknown>) : { type: "object" },
    })
  }
  return out
}

function toolResultPart(call: { id: string; name: string }, envelope: AgentToolResultEnvelope) {
  return {
    type: "tool-result",
    id: call.id,
    name: call.name,
    result: {
      type: envelope.is_error ? "error" : "text",
      value: typeof envelope.result === "string" ? envelope.result : JSON.stringify(envelope.result),
    },
  }
}


async function health(): Promise<Response> {
  return cors(
    json({
      ok: true,
      service: "appforge-llm-bridge",
      version: VERSION,
    }),
  )
}

async function ready(): Promise<Response> {
  return cors(json({ ok: true, service: "appforge-llm-bridge", version: VERSION }))
}

async function listProviders(request: Request): Promise<Response> {
  const url = new URL(request.url)
  const includeModels = url.searchParams.get("include_models") === "true"
  const config = await store.load()
  const entries = await registry.list()
  const statuses: ProviderStatus[] = entries.map((entry) =>
    registry.statusOf(entry, config.providers[entry.id], { includeModels }),
  )
  return cors(json({ providers: statuses }))
}

async function refreshCatalog(): Promise<Response> {
  const loaded = await registry.refreshCatalog()
  return cors(json({ ok: loaded, catalog_loaded: loaded, catalog_path: catalog.catalogPath() }))
}

function decodeParam(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

async function providerModels(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const entry = await registry.get(id)
  if (!entry) return cors(errorResponse(`Unknown provider '${id}'`, 404, "UNKNOWN_PROVIDER"))
  return cors(json({ id, name: entry.name, models: entry.models }))
}

async function upsertProvider(request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const entry = await registry.get(id)
  if (!entry) return cors(errorResponse(`Unknown provider '${id}'`, 404, "UNKNOWN_PROVIDER"))
  const body = await readJsonBody(request)
  const has = (key: string) => Object.prototype.hasOwnProperty.call(body, key)
  const requestedBaseURL = typeof body.baseURL === "string" ? body.baseURL.trim() : undefined
  if (
    id !== "openai-compatible"
    && requestedBaseURL
    && requestedBaseURL !== entry.base_url_default
  ) {
    return cors(errorResponse(
      "Built-in providers use their fixed local endpoint definition.",
      422,
      "CUSTOM_BASE_URL_NOT_ALLOWED",
    ))
  }
  await store.setProvider(id, {
    apiKey: has("apiKey") && typeof body.apiKey === "string" ? body.apiKey : undefined,
    clearApiKey: body.clearApiKey === true,
    baseURL: id === "openai-compatible" && has("baseURL")
      ? (typeof body.baseURL === "string" ? body.baseURL : null)
      : undefined,
    defaultModel: has("defaultModel")
      ? (typeof body.defaultModel === "string" ? body.defaultModel : null)
      : undefined,
  })
  const config = await store.load()
  return cors(json({ status: registry.statusOf(entry, config.providers[id]) }))
}

async function removeProvider(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  await store.deleteProvider(id)
  return cors(json({ ok: true, id }))
}

async function getActive(): Promise<Response> {
  return cors(json(await store.getActive()))
}

async function setActive(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const selection: ActiveSelection = {
    provider: typeof body.provider === "string" ? body.provider : null,
    model: typeof body.model === "string" ? body.model : null,
  }
  if (selection.provider && !(await registry.get(selection.provider))) {
    return cors(errorResponse(`Unknown provider '${selection.provider}'`, 404, "UNKNOWN_PROVIDER"))
  }
  const result = await store.setActive(selection)
  return cors(json(result))
}

async function testProvider(request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const entry = await registry.get(id)
  if (!entry) return cors(errorResponse(`Unknown provider '${id}'`, 404, "UNKNOWN_PROVIDER"))
  const body = await readJsonBody(request)
  const payload = body as unknown as TestRequest
  const stored = await store.getProvider(id)
  const requestedBaseURL = typeof payload.baseURL === "string" ? payload.baseURL.trim() : undefined
  const explicitKey = typeof payload.apiKey === "string" && payload.apiKey.length > 0
    ? payload.apiKey
    : undefined
  if (id !== "openai-compatible" && requestedBaseURL && requestedBaseURL !== entry.base_url_default) {
    return cors(errorResponse(
      "Built-in providers use their fixed local endpoint definition.",
      422,
      "CUSTOM_BASE_URL_NOT_ALLOWED",
    ))
  }
  if (
    id === "openai-compatible"
    && requestedBaseURL
    && requestedBaseURL !== stored?.baseURL
    && !explicitKey
  ) {
    return cors(errorResponse(
      "Testing a new custom endpoint requires an API key in the same request.",
      422,
      "EXPLICIT_KEY_REQUIRED_FOR_CUSTOM_ENDPOINT",
    ))
  }
  const tempConfig = {
    apiKey: explicitKey ?? stored?.apiKey,
    baseURL: id === "openai-compatible" ? (requestedBaseURL ?? stored?.baseURL) : undefined,
    defaultModel: stored?.defaultModel,
  }
  let resolved
  try {
    resolved = await registry.resolveForGeneration(id, payload.model, tempConfig)
  } catch (error) {
    const out: TestResponse = {
      ok: false,
      error: publicErrorMessage(error, "Provider configuration could not be resolved."),
    }
    return cors(json(out))
  }
  try {
    const result = await runOnce({
      model: resolved.model,
      system: "You are a connection test. Reply concisely.",
      prompt: "Reply with the single word: ok",
      generation: { maxTokens: 16, temperature: 0 },
    })
    const out: TestResponse = { ok: true, text: result.text.trim(), provider: id, model: resolved.modelId }
    return cors(json(out))
  } catch (error) {
    const message = publicErrorMessage(error, "Provider connection test failed.")
    const out: TestResponse = { ok: false, error: message, provider: id, model: resolved.modelId }
    return cors(json(out))
  }
}

async function generateHandler(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const payload = body as unknown as GenerateRequest
  if (!payload || typeof payload.prompt !== "string" || payload.prompt.length === 0) {
    return cors(errorResponse("`prompt` is required", 422, "INVALID_REQUEST"))
  }
  try {
    const result = await generate(payload, { signal: request.signal })
    return cors(json(result))
  } catch (error) {
    if (error instanceof BridgeLLMCancelled) {
      return cors(errorResponse(error.message, 499, "LLM_CANCELLED"))
    }
    const message = publicErrorMessage(error, "LLM generation failed.")
    const status = error instanceof BridgeLLMError ? 502 : 400
    return cors(errorResponse(message, status, "LLM_ERROR"))
  }
}

async function streamHandler(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const payload = body as unknown as GenerateRequest
  if (!payload || typeof payload.prompt !== "string" || payload.prompt.length === 0) {
    return cors(errorResponse("`prompt` is required", 422, "INVALID_REQUEST"))
  }

  const headers = sseHeaders()
  let heartbeat: ReturnType<typeof setInterval> | undefined
  let streamClosed = false
  const operationAbort = new AbortController()
  const abortFromRequest = () => operationAbort.abort()
  if (request.signal.aborted) operationAbort.abort()
  else request.signal.addEventListener("abort", abortFromRequest, { once: true })
  const streamBody = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder()
      const send = (event: string, data: unknown) => {
        if (streamClosed) return
        try {
          controller.enqueue(encoder.encode(`event: ${event}\n`))
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {
          streamClosed = true
          operationAbort.abort()
          if (heartbeat) clearInterval(heartbeat)
          throw new BridgeLLMCancelled("LLM stream client disconnected.")
        }
      }
      try {
        send("connected", { type: "connected", timestamp: Date.now() })
        heartbeat = setInterval(() => {
          if (streamClosed) return
          try {
            controller.enqueue(encoder.encode(`: keep-alive ${Date.now()}\n\n`))
          } catch {
            streamClosed = true
            operationAbort.abort()
            if (heartbeat) clearInterval(heartbeat)
          }
        }, SSE_HEARTBEAT_MS)
        const meta = await stream(payload, (event: StreamEvent) => {
          send(event.type, event)
        }, { signal: operationAbort.signal })
        send("done", meta)
      } catch (error) {
        if (error instanceof BridgeLLMCancelled) {
          send("cancelled", { type: "cancelled", message: error.message })
          return
        }
        const message = publicErrorMessage(error, "LLM streaming failed.")
        send("error", { type: "error", code: "LLM_STREAM_ERROR", message })
      } finally {
        request.signal.removeEventListener("abort", abortFromRequest)
        if (heartbeat) clearInterval(heartbeat)
        if (!streamClosed) {
          streamClosed = true
          try {
            controller.close()
          } catch {
            // The client disconnected while the model request was finishing.
          }
        }
      }
    },
    cancel() {
      streamClosed = true
      operationAbort.abort()
      request.signal.removeEventListener("abort", abortFromRequest)
      if (heartbeat) clearInterval(heartbeat)
    },
  })
  return new Response(streamBody, { status: 200, headers })
}

async function agentStart(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const prompt = typeof body.prompt === "string" ? body.prompt : ""
  if (!prompt) return cors(errorResponse("`prompt` is required", 422, "INVALID_REQUEST"))
  const id = randomSessionId()
  const now = Date.now()
  const session: AgentSession = {
    id,
    createdAt: now,
    lastActivityAt: now,
    system: typeof body.system === "string" ? body.system : undefined,
    provider: typeof body.provider === "string" ? body.provider : undefined,
    model: typeof body.model === "string" ? body.model : undefined,
    generation: body.generation && typeof body.generation === "object" ? (body.generation as GenerationOptions) : undefined,
    responseFormat: body.responseFormat && typeof body.responseFormat === "object" ? (body.responseFormat as Record<string, unknown>) : undefined,
    tools: asToolDefinitions(body.tools),
    messages: [{ role: "user", content: prompt }],
    pending: new Map(),
    announcedToolCalls: new Map(),
    queuedToolResults: new Map(),
    completedToolResults: new Map(),
    eventStreamActive: false,
    closed: false,
  }
  agentSessions.set(id, session)
  return cors(json({ session_id: id, id, tools: session.tools.map((tool) => tool.name) }))
}

async function agentEvents(request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const session = getAgentSession(id)
  if (!session) return cors(errorResponse(`Unknown agent session '${id}'`, 404, "UNKNOWN_AGENT_SESSION"))
  if (session.eventStreamActive) {
    return cors(errorResponse(`Agent session '${id}' already has an active event stream.`, 409, "AGENT_STREAM_ALREADY_ACTIVE"))
  }
  session.eventStreamActive = true
  touchAgentSession(session)
  const headers = sseHeaders()
  let heartbeat: ReturnType<typeof setInterval> | undefined
  let streamClosed = false
  const operationAbort = new AbortController()
  const abortFromRequest = () => operationAbort.abort()
  if (request.signal.aborted) operationAbort.abort()
  else request.signal.addEventListener("abort", abortFromRequest, { once: true })
  const streamBody = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder()
      const send = (event: string, data: unknown) => {
        if (streamClosed) return
        touchAgentSession(session)
        try {
          controller.enqueue(encoder.encode(`event: ${event}\n`))
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {
          streamClosed = true
          operationAbort.abort()
          if (heartbeat) clearInterval(heartbeat)
          throw new BridgeLLMCancelled("Agent event client disconnected.")
        }
      }
      try {
        send("connected", { type: "connected", session_id: id, timestamp: Date.now() })
        heartbeat = setInterval(() => {
          if (streamClosed) return
          try {
            touchAgentSession(session)
            controller.enqueue(encoder.encode(`: keep-alive ${Date.now()}\n\n`))
          } catch {
            streamClosed = true
            operationAbort.abort()
            if (heartbeat) clearInterval(heartbeat)
          }
        }, SSE_HEARTBEAT_MS)
        await runAgentSession(session, send, operationAbort.signal)
      } catch (error) {
        if (error instanceof BridgeLLMCancelled) {
          send("cancelled", { type: "cancelled", message: error.message })
          return
        }
        const message = publicErrorMessage(error, "Agent streaming failed.")
        send("error", { type: "error", code: "AGENT_STREAM_ERROR", message })
      } finally {
        request.signal.removeEventListener("abort", abortFromRequest)
        session.eventStreamActive = false
        touchAgentSession(session)
        if (heartbeat) clearInterval(heartbeat)
        if (!streamClosed) {
          streamClosed = true
          try {
            controller.close()
          } catch {
            // The client disconnected while the agent loop was finishing.
          }
        }
      }
    },
    cancel() {
      streamClosed = true
      operationAbort.abort()
      request.signal.removeEventListener("abort", abortFromRequest)
      if (heartbeat) clearInterval(heartbeat)
    },
  })
  return new Response(streamBody, { status: 200, headers })
}

async function runAgentSession(
  session: AgentSession,
  send: (event: string, data: unknown) => void,
  signal: AbortSignal,
): Promise<void> {
  let turns = 0
  let totalUsage: Record<string, unknown> = {}
  while (!session.closed) {
    if (signal.aborted) throw new BridgeLLMCancelled()
    touchAgentSession(session)
    const textParts: string[] = []
    const toolCalls: Array<{ id: string; name: string; input: unknown }> = []
    let finishReason = "stop"
    let usage: Record<string, unknown> = {}
    const req: GenerateRequest = {
      provider: session.provider,
      model: session.model,
      system: session.system,
      messages: session.messages,
      tools: session.tools,
      toolChoice: session.tools.length > 0 ? "auto" : "none",
      generation: session.generation,
      responseFormat: session.responseFormat,
    }
    await agentStreamImplementation(req, (event: StreamEvent) => {
      if (event.type === "text-delta" && typeof event.text === "string") {
        textParts.push(event.text)
        touchAgentSession(session)
        send("text_delta", { type: "text_delta", text: event.text })
        return
      }
      if (event.type === "tool-call") {
        const id = String(event.call_id || event.id || "")
        const name = String(event.name || "")
        const input = event.arguments ?? event.input ?? {}
        if (!id || !name) return
        toolCalls.push({ id, name, input })
        session.announcedToolCalls.set(id, name)
        touchAgentSession(session)
        send("tool_call", { type: "tool_call", call_id: id, id, name, arguments: input })
        return
      }
      if (event.type === "finish" || event.type === "step-finish") {
        if (typeof event.reason === "string" && event.reason) finishReason = event.reason
        if (event.usage && typeof event.usage === "object") usage = event.usage
      }
    }, { signal })
    totalUsage = addUsage(totalUsage, usage)
    const assistantContent: unknown[] = []
    const assistantText = textParts.join("")
    if (assistantText) assistantContent.push({ type: "text", text: assistantText })
    for (const call of toolCalls) {
      assistantContent.push({ type: "tool-call", id: call.id, name: call.name, input: call.input })
    }
    if (assistantContent.length > 0) {
      session.messages.push({ role: "assistant", content: assistantContent })
    }
    if (toolCalls.length === 0) {
      send("done", { type: "done", finish_reason: finishReason || "stop", turns, usage: totalUsage })
      return
    }
    turns += toolCalls.length
    if (turns > AGENT_MAX_TURNS) {
      send("error", { type: "error", code: "AGENT_TURN_BUDGET_EXCEEDED", message: "Agent tool-call turn budget exceeded." })
      return
    }
    for (const call of toolCalls) {
      const envelope = await waitForToolResult(session, call.id, call.name, signal)
      session.messages.push({ role: "tool", content: [toolResultPart(call, envelope)] })
      touchAgentSession(session)
      send("tool_result", { type: "tool_result", call_id: call.id, name: call.name, ok: !envelope.is_error })
    }
  }
  send("done", { type: "done", finish_reason: "closed", turns, usage: totalUsage })
}

function addUsage(
  current: Record<string, unknown>,
  next: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...current }
  for (const [key, value] of Object.entries(next)) {
    if (typeof value === "number" && Number.isFinite(value)) {
      const previous = typeof merged[key] === "number" ? Number(merged[key]) : 0
      merged[key] = previous + value
    } else if (key === "providerMetadata" && value && typeof value === "object") {
      merged[key] = value
    }
  }
  return merged
}

async function agentToolResult(request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const session = getAgentSession(id)
  if (!session) return cors(errorResponse(`Unknown agent session '${id}'`, 404, "UNKNOWN_AGENT_SESSION"))
  const body = await readJsonBody(request)
  const callId = typeof body.call_id === "string" ? body.call_id : typeof body.callId === "string" ? body.callId : ""
  if (!callId) return cors(errorResponse("`call_id` is required", 422, "INVALID_REQUEST"))
  const envelope = { result: body.result, is_error: Boolean(body.is_error ?? body.isError) }
  const completed = session.completedToolResults.get(callId)
  if (completed) {
    if (!sameToolResult(completed, envelope)) {
      return cors(errorResponse(`Tool call '${callId}' already has a different result.`, 409, "TOOL_RESULT_CONFLICT"))
    }
    touchAgentSession(session)
    return cors(json({ ok: true, duplicate: true, session_id: id, call_id: callId }))
  }
  const queued = session.queuedToolResults.get(callId)
  if (queued) {
    if (!sameToolResult(queued, envelope)) {
      return cors(errorResponse(`Tool call '${callId}' already has a different queued result.`, 409, "TOOL_RESULT_CONFLICT"))
    }
    touchAgentSession(session)
    return cors(json({ ok: true, duplicate: true, queued: true, session_id: id, call_id: callId }))
  }
  const pending = session.pending.get(callId)
  if (pending) {
    pending.resolve(envelope)
    return cors(json({ ok: true, session_id: id, call_id: callId }))
  }
  if (session.announcedToolCalls.has(callId)) {
    session.queuedToolResults.set(callId, envelope)
    touchAgentSession(session)
    return cors(json({ ok: true, queued: true, session_id: id, call_id: callId }))
  }
  return cors(errorResponse(`No announced or pending tool call '${callId}'`, 404, "UNKNOWN_TOOL_CALL"))
}

async function agentDelete(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const id = decodeParam(match[1] ?? "")
  const session = getAgentSession(id)
  if (session) closeAgentSession(session)
  return cors(json({ ok: true, session_id: id }))
}


async function oauthProviders(): Promise<Response> {
  return cors(json({ providers: oauth.listOAuthProviders() }))
}

async function oauthStart(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const providerId = typeof body.provider === "string" ? body.provider : ""
  const method = body.method === "browser" || body.method === "device-code" ? body.method : "browser"
  if (!oauth.isOAuthProvider(providerId)) {
    return cors(errorResponse(`No OAuth handler for '${providerId}'`, 404, "UNKNOWN_OAUTH_PROVIDER"))
  }
  try {
    const result = await oauth.startOAuthFlow(providerId, method as "browser" | "device-code")
    return cors(json(result))
  } catch {
    return cors(errorResponse("OAuth authorization could not be started.", 500, "OAUTH_START_FAILED"))
  }
}

async function oauthPoll(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const providerId = decodeParam(match[1] ?? "")
  const pollId = decodeParam(match[2] ?? "")
  const result = oauth.pollOAuthFlow(providerId, pollId)
  if (result.status === "success") {
    if (!result.credential) {
      return cors(errorResponse("OAuth completed without a credential.", 502, "OAUTH_RESULT_INVALID"))
    }
    try {
      await store.setOAuthCredential(providerId, result.credential)
    } catch {
      return cors(errorResponse(
        "OAuth credential could not be stored securely.",
        500,
        "OAUTH_CREDENTIAL_STORAGE_FAILED",
      ))
    }
  }
  return cors(json(publicOAuthPollResult(result, providerId)))
}

async function oauthRefresh(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const providerId = decodeParam(match[1] ?? "")
  if (!oauth.isOAuthProvider(providerId)) {
    return cors(errorResponse(`No OAuth handler for '${providerId}'`, 404, "UNKNOWN_OAUTH_PROVIDER"))
  }
  const existing = await store.getOAuthCredential(providerId)
  if (!existing) {
    return cors(errorResponse(`No OAuth credential stored for '${providerId}'`, 404, "NO_OAUTH_CREDENTIAL"))
  }
  let refreshed: OAuthCredential
  try {
    refreshed = await oauth.refreshOAuthToken(providerId, existing.refresh)
  } catch {
    return cors(errorResponse("OAuth credential refresh failed.", 500, "OAUTH_REFRESH_FAILED"))
  }
  try {
    await store.setOAuthCredential(providerId, refreshed)
  } catch {
    return cors(errorResponse(
      "Refreshed OAuth credential could not be stored securely.",
      500,
      "OAUTH_CREDENTIAL_STORAGE_FAILED",
    ))
  }
  return cors(json(publicOAuthRefreshResult(providerId, refreshed)))
}

function loopbackHost(value: string): boolean {
  const trimmed = value.trim().toLowerCase()
  if (trimmed.startsWith("[")) return LOOPBACK_HOSTS.has(trimmed.slice(1, trimmed.indexOf("]")))
  return LOOPBACK_HOSTS.has(trimmed.split(":", 1)[0] ?? "")
}

function sameLoopbackOrigin(requestUrl: URL, value: string): boolean {
  try {
    const url = new URL(value)
    const port = url.port || (url.protocol === "https:" ? "443" : "80")
    const requestPort = requestUrl.port || (requestUrl.protocol === "https:" ? "443" : "80")
    return (
      (url.protocol === "http:" || url.protocol === "https:")
      && LOOPBACK_HOSTS.has(url.hostname)
      && url.protocol === requestUrl.protocol
      && url.hostname === requestUrl.hostname
      && port === requestPort
    )
  } catch {
    return false
  }
}

function tokenMatches(supplied: string | null, expected: string): boolean {
  if (!supplied) return false
  const suppliedHash = createHash("sha256").update(supplied).digest()
  const expectedHash = createHash("sha256").update(expected).digest()
  return timingSafeEqual(suppliedHash, expectedHash)
}

function configuredBridgeToken(): string {
  const token = process.env.APPFORGE_LLM_BRIDGE_TOKEN?.trim() ?? ""
  if (token.length < 32) {
    throw new Error("APPFORGE_LLM_BRIDGE_TOKEN must be set to at least 32 characters")
  }
  return token
}

function publicInternalError(): Response {
  return cors(errorResponse("The bridge could not complete the request.", 500, "INTERNAL"))
}

async function dispatch(request: Request, authToken: string): Promise<Response> {
  const url = new URL(request.url)
  const host = request.headers.get("host") || url.host
  if (!loopbackHost(host)) {
    return cors(errorResponse("Forbidden Host header.", 403, "FORBIDDEN_HOST"))
  }
  const origin = request.headers.get("origin")
  const fetchSite = request.headers.get("sec-fetch-site")?.toLowerCase()
  if ((origin && !sameLoopbackOrigin(url, origin)) || fetchSite === "cross-site") {
    return cors(errorResponse("Cross-origin bridge requests are forbidden.", 403, "FORBIDDEN_ORIGIN"))
  }
  const path = url.pathname.replace(/\/+$/, "") || "/"
  if (path !== "/health" && !tokenMatches(request.headers.get(BRIDGE_TOKEN_HEADER), authToken)) {
    return cors(errorResponse("Bridge authentication is required.", 401, "BRIDGE_AUTH_REQUIRED"))
  }
  if (request.method === "OPTIONS") {
    return cors(errorResponse("CORS preflight is not supported.", 405, "METHOD_NOT_ALLOWED"))
  }
  for (const route of ROUTES) {
    if (route.method !== request.method) continue
    const match = route.pattern.exec(url.pathname)
    if (!match) continue
    try {
      const response = await route.handler(request, match)
      return response
    } catch (error) {
      if (error instanceof BridgeHttpError) return cors(errorResponse(error.message, error.status, error.code))
      return publicInternalError()
    }
  }
  return cors(errorResponse("Not found", 404, "NOT_FOUND"))
}

function isLongRunningRequest(request: Request): boolean {
  const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/"
  if (path === "/generate" || path === "/stream") return true
  if (/^\/providers\/[^/]+\/test$/.test(path)) return true
  if (/^\/agent\/[^/]+\/events$/.test(path)) return true
  return false
}

export function start(): void {
  if (!LOOPBACK_HOSTS.has(HOST.toLowerCase())) {
    throw new Error("APPFORGE_LLM_BRIDGE_HOST must be a loopback address")
  }
  const authToken = configuredBridgeToken()
  const server = Bun.serve({
    port: PORT,
    hostname: HOST,
    idleTimeout: idleTimeoutSeconds(),
    async fetch(request, server) {
      // Bun closes otherwise healthy in-flight requests after 10 seconds of
      // silence by default. LLM generation and SSE streams can legitimately
      // be quiet for much longer, so disable the per-request idle timer while
      // retaining a bounded global timeout for ordinary management routes.
      if (isLongRunningRequest(request)) server.timeout(request, 0)
      return dispatch(request, authToken)
    },
  })
  console.log(
    `[llm-bridge] listening on http://${server.hostname}:${server.port} `
    + `(config: ${store.configPath()}, idleTimeout: ${idleTimeoutSeconds()}s, SSE heartbeat: ${SSE_HEARTBEAT_MS}ms)`,
  )
}

export function createApp() {
  const authToken = configuredBridgeToken()
  return { fetch: (request: Request) => dispatch(request, authToken) }
}
