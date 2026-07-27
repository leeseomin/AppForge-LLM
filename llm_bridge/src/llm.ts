import { Effect, Layer, Stream } from "effect"
import { LLM, LLMClient } from "@opencode-ai/llm"
import type { Model } from "@opencode-ai/llm"
import { RequestExecutor, WebSocketExecutor } from "@opencode-ai/llm/route"
import * as registry from "./registry"
import * as config from "./config"
import type { GenerateRequest, GenerateResponse, GenerationOptions } from "./types"

const deps = Layer.mergeAll(RequestExecutor.defaultLayer, WebSocketExecutor.layer)
const clientLayer = LLMClient.layer.pipe(Layer.provide(deps))

export class BridgeLLMError extends Error {
  constructor(message: string, readonly detail?: unknown) {
    super(message)
    this.name = "BridgeLLMError"
  }
}

export class BridgeLLMCancelled extends Error {
  constructor(message = "LLM generation cancelled.") {
    super(message)
    this.name = "BridgeLLMCancelled"
  }
}

interface ExecutionOptions {
  signal?: AbortSignal
}

interface ResolvedRequest {
  providerId: string
  modelId: string
  effect: ReturnType<typeof LLM.request>
}

async function resolveRequest(req: GenerateRequest): Promise<ResolvedRequest> {
  const active = await config.getActive()
  const providerId = req.provider || active.provider || ""
  if (!providerId) {
    throw new BridgeLLMError("No provider selected. Configure and activate a provider first.")
  }
  const stored = await config.getProvider(providerId)
  const resolved = await registry.resolveForGeneration(providerId, req.model, stored)
  const generation: GenerationOptions | undefined = req.generation
  const requestInput: Record<string, unknown> = {
    model: resolved.model,
    system: req.system,
    generation,
  }
  if (Array.isArray(req.messages) && req.messages.length > 0) {
    requestInput.messages = req.messages
  } else {
    requestInput.prompt = req.prompt ?? ""
  }
  if (Array.isArray(req.tools) && req.tools.length > 0) {
    requestInput.tools = req.tools.map((tool) => ({
      name: tool.name,
      description: tool.description ?? tool.name,
      inputSchema: tool.parameters ?? { type: "object" },
    }))
  }
  if (req.toolChoice) requestInput.toolChoice = req.toolChoice
  if (req.responseFormat) requestInput.responseFormat = req.responseFormat
  const built = LLM.request(requestInput as never)
  return { providerId: resolved.providerId, modelId: resolved.modelId, effect: built }
}

function describeError(error: unknown): string {
  if (!error) return "Unknown error"
  // coco LLMError carries a structured `reason`; fall back to its message.
  const anyError = error as { _tag?: string; message?: string; reason?: { _tag?: string; message?: string } }
  const reason = anyError.reason
  if (reason && (reason.message || reason._tag)) {
    return `${reason._tag ? `[${reason._tag}] ` : ""}${reason.message ?? ""}`.trim() || String(error)
  }
  if (anyError._tag === "LLM.Error" && anyError.message) return anyError.message
  return anyError.message || String(error)
}

function finishReasonFromEvents(events: ReadonlyArray<unknown>): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i] as { type?: string; reason?: string }
    if (event && event.type === "finish" && event.reason) return event.reason
  }
  return "unknown"
}

function plainUsage(usage: unknown): Record<string, unknown> {
  try {
    return JSON.parse(JSON.stringify(usage ?? {})) as Record<string, unknown>
  } catch {
    return {}
  }
}

export interface RunInput {
  model: Model
  system?: string
  prompt: string
  generation?: GenerationOptions
}

export async function runOnce(input: RunInput): Promise<{ text: string; finishReason: string; usage: Record<string, unknown> }> {
  const request = LLM.request({
    model: input.model,
    system: input.system,
    prompt: input.prompt,
    generation: input.generation,
  })
  try {
    const response = await Effect.runPromise(LLM.generate(request).pipe(Effect.provide(clientLayer)))
    const events = response.events as ReadonlyArray<unknown>
    return {
      text: response.text ?? "",
      finishReason: finishReasonFromEvents(events),
      usage: plainUsage(response.usage),
    }
  } catch (error) {
    throw new BridgeLLMError(describeError(error), error)
  }
}

export { resolveRequest, type ResolvedRequest }

export async function generate(req: GenerateRequest, options: ExecutionOptions = {}): Promise<GenerateResponse> {
  if (options.signal?.aborted) {
    throw new BridgeLLMCancelled()
  }
  const resolved = await resolveRequest(req)
  if (options.signal?.aborted) {
    throw new BridgeLLMCancelled()
  }
  try {
    const response = await Effect.runPromise(LLM.generate(resolved.effect).pipe(Effect.provide(clientLayer)), {
      signal: options.signal,
    })
    const events = response.events as ReadonlyArray<unknown>
    return {
      provider: resolved.providerId,
      model: resolved.modelId,
      text: response.text ?? "",
      finishReason: finishReasonFromEvents(events),
      usage: plainUsage(response.usage),
    }
  } catch (error) {
    if (options.signal?.aborted) {
      throw new BridgeLLMCancelled()
    }
    throw new BridgeLLMError(describeError(error), error)
  }
}

export interface StreamEvent {
  type: string
  text?: string
  reason?: string
  usage?: Record<string, unknown>
  call_id?: string
  id?: string
  name?: string
  arguments?: unknown
  input?: unknown
  raw?: unknown
}

export async function stream(
  req: GenerateRequest,
  onEvent: (event: StreamEvent) => Promise<void> | void,
  options: ExecutionOptions = {},
): Promise<{ provider: string; model: string }> {
  if (options.signal?.aborted) {
    throw new BridgeLLMCancelled()
  }
  const resolved = await resolveRequest(req)
  if (options.signal?.aborted) {
    throw new BridgeLLMCancelled()
  }
  const program = LLM.stream(resolved.effect).pipe(
    Stream.runForEach((event) =>
      Effect.sync(() => {
        const normalized = normalizeEvent(event)
        if (normalized) void onEvent(normalized)
      }),
    ),
    Effect.provide(clientLayer),
  )
  try {
    await Effect.runPromise(program, { signal: options.signal })
  } catch (error) {
    if (options.signal?.aborted) {
      throw new BridgeLLMCancelled()
    }
    throw new BridgeLLMError(describeError(error), error)
  }
  return { provider: resolved.providerId, model: resolved.modelId }
}

function normalizeEvent(event: unknown): StreamEvent | null {
  const e = event as { type?: string; text?: string; reason?: string; usage?: unknown; id?: string; name?: string; input?: unknown }
  if (!e || !e.type) return null
  const out: StreamEvent = { type: e.type, raw: event }
  if (e.type === "text-delta" && typeof e.text === "string") out.text = e.text
  if (e.type === "tool-call") {
    out.type = "tool-call"
    out.call_id = String(e.id ?? "")
    out.id = String(e.id ?? "")
    out.name = String(e.name ?? "")
    out.arguments = e.input ?? {}
    out.input = e.input ?? {}
  }
  if (e.type === "finish" || e.type === "step-finish") {
    if (typeof e.reason === "string") out.reason = e.reason
    if (e.usage !== undefined) out.usage = plainUsage(e.usage)
  }
  return out
}
