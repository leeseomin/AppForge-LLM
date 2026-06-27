import type { Model } from "@opencode-ai/llm"
import * as OpenAI from "@opencode-ai/llm/providers/openai"
import * as Anthropic from "@opencode-ai/llm/providers/anthropic"
import * as Google from "@opencode-ai/llm/providers/google"
import * as OpenRouter from "@opencode-ai/llm/providers/openrouter"
import * as XAI from "@opencode-ai/llm/providers/xai"
import * as OpenAICompatible from "@opencode-ai/llm/providers/openai-compatible"
import type {
  ProviderDescriptor,
  ProviderKind,
  ProviderModel,
  ProviderStatus,
  StoredProviderConfig,
} from "./types"

export interface BuildOptions {
  apiKey: string
  baseURL?: string
  providerLabel?: string
}

export interface RegistryEntry extends ProviderDescriptor {
  build: (modelId: string, options: BuildOptions) => Model
}

const m = (id: string, name?: string): ProviderModel => ({ id, name })

const ENTRIES: RegistryEntry[] = [
  {
    id: "openai",
    name: "OpenAI",
    kind: "api-key",
    env_key: "OPENAI_API_KEY",
    docs_url: "https://platform.openai.com/api-keys",
    models: [
      m("gpt-4o-mini", "GPT-4o mini"),
      m("gpt-4o", "GPT-4o"),
      m("gpt-4.1-mini", "GPT-4.1 mini"),
      m("gpt-4.1", "GPT-4.1"),
      m("o4-mini", "o4-mini"),
    ],
    build: (id, options) => OpenAI.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "anthropic",
    name: "Anthropic",
    kind: "api-key",
    env_key: "ANTHROPIC_API_KEY",
    docs_url: "https://console.anthropic.com/settings/keys",
    models: [
      m("claude-3-5-haiku-latest", "Claude 3.5 Haiku"),
      m("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet"),
      m("claude-sonnet-4-20250514", "Claude Sonnet 4"),
      m("claude-opus-4-20250514", "Claude Opus 4"),
      m("claude-3-7-sonnet-latest", "Claude 3.7 Sonnet"),
    ],
    build: (id, options) => Anthropic.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "google",
    name: "Google Gemini",
    kind: "api-key",
    env_key: "GOOGLE_GENERATIVE_AI_API_KEY",
    docs_url: "https://aistudio.google.com/app/apikey",
    models: [
      m("gemini-1.5-flash", "Gemini 1.5 Flash"),
      m("gemini-1.5-pro", "Gemini 1.5 Pro"),
      m("gemini-2.0-flash", "Gemini 2.0 Flash"),
      m("gemini-2.5-flash", "Gemini 2.5 Flash"),
      m("gemini-2.5-pro", "Gemini 2.5 Pro"),
    ],
    build: (id, options) => Google.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    kind: "api-key",
    env_key: "OPENROUTER_API_KEY",
    base_url_default: "https://openrouter.ai/api/v1",
    docs_url: "https://openrouter.ai/settings/keys",
    models: [
      m("openrouter/auto", "Auto-router"),
      m("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet"),
      m("openai/gpt-4o-mini", "GPT-4o mini"),
      m("google/gemini-2.0-flash-001", "Gemini 2.0 Flash"),
    ],
    build: (id, options) => OpenRouter.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "xai",
    name: "xAI (Grok)",
    kind: "api-key",
    env_key: "XAI_API_KEY",
    base_url_default: "https://api.x.ai/v1",
    docs_url: "https://console.x.ai",
    models: [m("grok-2", "Grok 2"), m("grok-2-latest", "Grok 2 Latest"), m("grok-beta", "Grok Beta")],
    build: (id, options) => XAI.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    kind: "openai-compatible",
    base_url_default: "https://api.deepseek.com/v1",
    docs_url: "https://platform.deepseek.com/api_keys",
    default_model: "deepseek-v4-pro",
    models: [
      m("deepseek-v4-pro", "DeepSeek V4 Pro"),
      m("deepseek-v4-flash", "DeepSeek V4 Flash"),
      m("deepseek-chat", "DeepSeek Chat (legacy)"),
      m("deepseek-reasoner", "DeepSeek Reasoner (legacy)"),
    ],
    build: (id, options) => OpenAICompatible.deepseek.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "groq",
    name: "Groq",
    kind: "openai-compatible",
    base_url_default: "https://api.groq.com/openai/v1",
    docs_url: "https://console.groq.com/keys",
    models: [m("llama-3.3-70b-versatile", "Llama 3.3 70B"), m("llama-3.1-8b-instant", "Llama 3.1 8B")],
    build: (id, options) => OpenAICompatible.groq.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "cerebras",
    name: "Cerebras",
    kind: "openai-compatible",
    base_url_default: "https://api.cerebras.ai/v1",
    docs_url: "https://cloud.cerebras.ai",
    models: [m("llama3.1-70b", "Llama 3.1 70B"), m("llama3.1-8b", "Llama 3.1 8B")],
    build: (id, options) => OpenAICompatible.cerebras.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "togetherai",
    name: "Together AI",
    kind: "openai-compatible",
    base_url_default: "https://api.together.xyz/v1",
    docs_url: "https://api.together.ai/settings/api-keys",
    models: [m("meta.llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B Turbo")],
    build: (id, options) => OpenAICompatible.togetherai.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "fireworks",
    name: "Fireworks AI",
    kind: "openai-compatible",
    base_url_default: "https://api.fireworks.ai/inference/v1",
    docs_url: "https://fireworks.ai/account/api-keys",
    models: [m("accounts/fireworks/models/llama-v3p1-70b-instruct", "Llama 3.1 70B")],
    build: (id, options) => OpenAICompatible.fireworks.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "deepinfra",
    name: "DeepInfra",
    kind: "openai-compatible",
    base_url_default: "https://api.deepinfra.com/v1/openai",
    docs_url: "https://deepinfra.com/dash/api_keys",
    models: [m("meta-llama/Meta-Llama-3.1-70B-Instruct", "Llama 3.1 70B")],
    build: (id, options) => OpenAICompatible.deepinfra.configure({ apiKey: options.apiKey, baseURL: options.baseURL }).model(id),
  },
  {
    id: "openai-compatible",
    name: "OpenAI 호환 (사용자 지정)",
    kind: "openai-compatible",
    base_url_required: true,
    docs_url: "https://platform.openai.com/docs/api-reference",
    models: [],
    build: (id, options) =>
      OpenAICompatible.configure({
        apiKey: options.apiKey,
        baseURL: options.baseURL ?? "",
        provider: options.providerLabel ?? "openai-compatible",
      }).model(id),
  },
]

const BY_ID: Map<string, RegistryEntry> = new Map(ENTRIES.map((entry) => [entry.id, entry]))

export function list(): RegistryEntry[] {
  return ENTRIES
}

export function get(id: string): RegistryEntry | undefined {
  return BY_ID.get(id)
}

export function kindOf(entry: RegistryEntry): ProviderKind {
  return entry.kind
}

function envKey(entry: RegistryEntry): string | undefined {
  return entry.env_key
}

function resolveKey(entry: RegistryEntry, stored: StoredProviderConfig | undefined): {
  value: string
  source: "stored" | "env" | "none"
} {
  if (stored?.apiKey && stored.apiKey.length > 0) return { value: stored.apiKey, source: "stored" }
  const env = envKey(entry)
  if (env) {
    const fromEnv = process.env[env]
    if (fromEnv && fromEnv.length > 0) return { value: fromEnv, source: "env" }
  }
  return { value: "", source: "none" }
}

function resolveBaseURL(entry: RegistryEntry, stored: StoredProviderConfig | undefined): string | undefined {
  if (stored?.baseURL && stored.baseURL.length > 0) return stored.baseURL
  return entry.base_url_default
}

function defaultModelOf(entry: RegistryEntry, stored: StoredProviderConfig | undefined): string | null {
  const storedDefault = stored?.defaultModel
  if (
    entry.id === "deepseek" &&
    (!storedDefault || storedDefault === "deepseek-chat" || storedDefault === "deepseek-reasoner")
  ) {
    return entry.default_model ?? storedDefault ?? null
  }
  return storedDefault ?? entry.default_model ?? null
}

export function statusOf(entry: RegistryEntry, stored: StoredProviderConfig | undefined): ProviderStatus {
  const key = resolveKey(entry, stored)
  const baseURL = resolveBaseURL(entry, stored)
  const hasBaseURL = entry.base_url_required ? Boolean(baseURL) : true
  const configured = key.value.length > 0 && hasBaseURL
  return {
    id: entry.id,
    name: entry.name,
    kind: entry.kind,
    env_key: entry.env_key,
    base_url: baseURL ?? null,
    base_url_required: entry.base_url_required,
    base_url_default: entry.base_url_default,
    docs_url: entry.docs_url,
    has_key: key.value.length > 0,
    key_source: key.source,
    default_model: defaultModelOf(entry, stored),
    configured,
    models: entry.models,
  }
}

export interface ResolvedModel {
  model: Model
  providerId: string
  modelId: string
  apiKey: string
  baseURL?: string
}

export function resolveForGeneration(
  providerId: string,
  modelId: string | undefined,
  stored: StoredProviderConfig | undefined,
): ResolvedModel {
  const entry = BY_ID.get(providerId)
  if (!entry) throw new BridgeRegistryError(`Unknown provider '${providerId}'`)
  const key = resolveKey(entry, stored)
  if (!key.value) {
    throw new BridgeRegistryError(
      entry.env_key
        ? `No API key for '${providerId}'. Store one or set ${entry.env_key}.`
        : `No API key stored for '${providerId}'.`,
    )
  }
  if (entry.base_url_required && !resolveBaseURL(entry, stored)) {
    throw new BridgeRegistryError(`Provider '${providerId}' requires a base URL.`)
  }
  const chosenModel = modelId || defaultModelOf(entry, stored) || entry.models[0]?.id
  if (!chosenModel) throw new BridgeRegistryError(`No model specified for '${providerId}'.`)
  const model = entry.build(chosenModel, {
    apiKey: key.value,
    baseURL: resolveBaseURL(entry, stored),
    providerLabel: providerId,
  })
  return { model, providerId, modelId: chosenModel, apiKey: key.value, baseURL: resolveBaseURL(entry, stored) }
}

export class BridgeRegistryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "BridgeRegistryError"
  }
}
