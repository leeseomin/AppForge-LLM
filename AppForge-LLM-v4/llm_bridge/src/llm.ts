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
  const resolved = registry.resolveForGeneration(providerId, req.model, stored)
  const generation: GenerationOptions | undefined = req.generation
  const built = LLM.request({
    model: resolved.model,
    system: req.system,
    prompt: req.prompt,
    generation,
  })
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

export async function generate(req: GenerateRequest): Promise<GenerateResponse> {
  const resolved = await resolveRequest(req)
  try {
    const response = await Effect.runPromise(LLM.generate(resolved.effect).pipe(Effect.provide(clientLayer)))
    const events = response.events as ReadonlyArray<unknown>
    return {
      provider: resolved.providerId,
      model: resolved.modelId,
      text: response.text ?? "",
      finishReason: finishReasonFromEvents(events),
      usage: plainUsage(response.usage),
    }
  } catch (error) {
    throw new BridgeLLMError(describeError(error), error)
  }
}

export interface StreamEvent {
  type: string
  text?: string
  reason?: string
  usage?: Record<string, unknown>
  raw?: unknown
}

export async function stream(
  req: GenerateRequest,
  onEvent: (event: StreamEvent) => Promise<void> | void,
): Promise<{ provider: string; model: string }> {
  const resolved = await resolveRequest(req)
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
    await Effect.runPromise(program)
  } catch (error) {
    throw new BridgeLLMError(describeError(error), error)
  }
  return { provider: resolved.providerId, model: resolved.modelId }
}

function normalizeEvent(event: unknown): StreamEvent | null {
  const e = event as { type?: string; text?: string; reason?: string; usage?: unknown }
  if (!e || !e.type) return null
  const out: StreamEvent = { type: e.type }
  if (e.type === "text-delta" && typeof e.text === "string") out.text = e.text
  if (e.type === "finish" || e.type === "step-finish") {
    if (typeof e.reason === "string") out.reason = e.reason
    if (e.usage !== undefined) out.usage = plainUsage(e.usage)
  }
  return out
}
