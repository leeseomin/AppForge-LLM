import * as registry from "./registry"
import * as store from "./config"
import * as catalog from "./catalog"
import * as oauth from "./oauth"
import { BridgeLLMError, generate, runOnce, stream, type StreamEvent } from "./llm"
import { VERSION } from "./version"
import type {
  ActiveSelection,
  GenerateRequest,
  ProviderStatus,
  TestRequest,
  TestResponse,
} from "./types"

const PORT = Number(process.env.PORT || process.env.APPFORGE_LLM_BRIDGE_PORT || 8788)
const HOST = process.env.HOST || process.env.APPFORGE_LLM_BRIDGE_HOST || "127.0.0.1"

type Handler = (request: Request, match: RegExpMatchArray) => Promise<Response> | Response

interface Route {
  method: string
  pattern: RegExp
  handler: Handler
}

const ROUTES: Route[] = [
  { method: "GET", pattern: /^\/health\/?$/, handler: health },
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

function cors(response: Response): Response {
  response.headers.set("access-control-allow-methods", "GET, POST, PUT, DELETE, OPTIONS")
  response.headers.set("access-control-allow-headers", "content-type, authorization")
  response.headers.set("vary", "origin")
  return response
}

async function readJsonBody(request: Request): Promise<Record<string, unknown>> {
  if (!request.body) return {}
  const text = await request.text()
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

async function health(): Promise<Response> {
  const active = await store.getActive()
  return cors(
    json({
      ok: true,
      service: "appforge-llm-bridge",
      version: VERSION,
      config_path: store.configPath(),
      catalog_path: catalog.catalogPath(),
      catalog_loaded: registry.isCatalogLoaded(),
      active,
    }),
  )
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
  await store.setProvider(id, {
    apiKey: typeof body.apiKey === "string" ? body.apiKey : null,
    baseURL: typeof body.baseURL === "string" ? body.baseURL : null,
    defaultModel: typeof body.defaultModel === "string" ? body.defaultModel : null,
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
  const tempConfig = {
    apiKey: payload.apiKey ?? stored?.apiKey,
    baseURL: payload.baseURL ?? stored?.baseURL,
    defaultModel: stored?.defaultModel,
  }
  let resolved
  try {
    resolved = await registry.resolveForGeneration(id, payload.model, tempConfig)
  } catch (error) {
    const out: TestResponse = { ok: false, error: error instanceof Error ? error.message : String(error) }
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
    const message = error instanceof BridgeLLMError ? error.message : error instanceof Error ? error.message : String(error)
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
    const result = await generate(payload)
    return cors(json(result))
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
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

  const headers = new Headers({
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store",
    connection: "keep-alive",
  })
  const streamBody = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder()
      const send = (event: string, data: unknown) => {
        controller.enqueue(encoder.encode(`event: ${event}\n`))
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
      }
      try {
        const meta = await stream(payload, (event: StreamEvent) => {
          send(event.type, event)
        })
        send("done", meta)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        send("error", { message })
      } finally {
        controller.close()
      }
    },
  })
  return new Response(streamBody, { status: 200, headers })
}

async function oauthProviders(): Promise<Response> {
  return cors(json({ providers: oauth.listOAuthProviders() }))
}

async function oauthStart(request: Request): Promise<Response> {
  const body = await readJsonBody(request)
  const providerId = typeof body.provider === "string" ? body.provider : ""
  const method = body.method === "browser" || body.method === "device-code" ? body.method : "browser"
  const enterpriseDomain = typeof body.enterpriseDomain === "string" ? body.enterpriseDomain : undefined
  if (!oauth.isOAuthProvider(providerId)) {
    return cors(errorResponse(`No OAuth handler for '${providerId}'`, 404, "UNKNOWN_OAUTH_PROVIDER"))
  }
  try {
    const result = await oauth.startOAuthFlow(providerId, method as "browser" | "device-code", { enterpriseDomain })
    return cors(json(result))
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return cors(errorResponse(message, 500, "OAUTH_START_FAILED"))
  }
}

async function oauthPoll(_request: Request, match: RegExpMatchArray): Promise<Response> {
  const providerId = decodeParam(match[1] ?? "")
  const pollId = decodeParam(match[2] ?? "")
  const result = oauth.pollOAuthFlow(providerId, pollId)
  if (result.status === "success" && result.credential && result.provider) {
    try {
      await store.setOAuthCredential(result.provider, result.credential)
    } catch {
      // best-effort persist; the credential is still returned to the caller
    }
  }
  return cors(json(result))
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
  try {
    const refreshed = await oauth.refreshOAuthToken(providerId, existing.refresh)
    await store.setOAuthCredential(providerId, refreshed)
    return cors(json({ ok: true, provider: providerId, credential: refreshed }))
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return cors(errorResponse(message, 500, "OAUTH_REFRESH_FAILED"))
  }
}

async function dispatch(request: Request): Promise<Response> {
  if (request.method === "OPTIONS") {
    return cors(new Response(null, { status: 204 }))
  }
  const url = new URL(request.url)
  for (const route of ROUTES) {
    if (route.method !== request.method) continue
    const match = route.pattern.exec(url.pathname)
    if (!match) continue
    try {
      const response = await route.handler(request, match)
      return response
    } catch (error) {
      if (error instanceof BridgeHttpError) return cors(errorResponse(error.message, error.status, error.code))
      const message = error instanceof Error ? error.message : String(error)
      return cors(errorResponse(message, 500, "INTERNAL"))
    }
  }
  return cors(errorResponse("Not found", 404, "NOT_FOUND"))
}

export function start(): void {
  const server = Bun.serve({
    port: PORT,
    hostname: HOST,
    async fetch(request) {
      return dispatch(request)
    },
  })
  console.log(`[llm-bridge] listening on http://${server.hostname}:${server.port} (config: ${store.configPath()})`)
}

export function createApp() {
  return { fetch: dispatch }
}
