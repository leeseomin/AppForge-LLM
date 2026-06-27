import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { mkdir, readFile, stat, writeFile } from "node:fs/promises"
import type { ActiveSelection, StoredProviderConfig } from "./types"

const DEFAULT_CONFIG_DIR =
  process.env.APPFORGE_LLM_CONFIG_DIR ||
  process.env.APPFORGE_DATA_DIR ||
  join(homedir(), ".appforge", "llm")
const CONFIG_PATH =
  process.env.APPFORGE_LLM_CONFIG || join(DEFAULT_CONFIG_DIR, "providers.json")

export interface BridgeConfig {
  providers: Record<string, StoredProviderConfig>
  active: ActiveSelection
}

const EMPTY: BridgeConfig = { providers: {}, active: { provider: null, model: null } }

let cache: BridgeConfig | null = null
let loaded = false

export function configPath(): string {
  return CONFIG_PATH
}

async function ensureDir(): Promise<void> {
  await mkdir(dirname(CONFIG_PATH), { recursive: true })
}

async function applyPerms(): Promise<void> {
  try {
    await chmod0600(CONFIG_PATH)
  } catch {
    // Permissions are best-effort on some filesystems; never block writes.
  }
}

async function chmod0600(path: string): Promise<void> {
  const { chmod } = await import("node:fs/promises")
  await chmod(path, 0o600)
}

export async function load(): Promise<BridgeConfig> {
  if (loaded && cache) return cache
  try {
    const raw = await readFile(CONFIG_PATH, "utf8")
    const parsed = JSON.parse(raw) as Partial<BridgeConfig>
    cache = {
      providers: parsed.providers && typeof parsed.providers === "object" ? (parsed.providers as BridgeConfig["providers"]) : {},
      active: {
        provider: parsed.active?.provider ?? null,
        model: parsed.active?.model ?? null,
      },
    }
  } catch {
    cache = { providers: { ...EMPTY.providers }, active: { ...EMPTY.active } }
  }
  loaded = true
  return cache
}

export async function save(config: BridgeConfig): Promise<void> {
  await ensureDir()
  const payload = JSON.stringify(config, null, 2)
  await writeFile(CONFIG_PATH, payload, "utf8")
  await applyPerms()
  cache = config
  loaded = true
}

export async function getProvider(id: string): Promise<StoredProviderConfig | undefined> {
  const config = await load()
  return config.providers[id]
}

export async function setProvider(id: string, input: {
  apiKey?: string | null
  baseURL?: string | null
  defaultModel?: string | null
}): Promise<StoredProviderConfig> {
  const config = await load()
  const existing = config.providers[id] ?? {}
  // An empty/null apiKey means "keep existing"; send empty string "" to clear.
  const next: StoredProviderConfig = {
    ...existing,
    baseURL: input.baseURL === undefined ? existing.baseURL : input.baseURL ? input.baseURL : undefined,
    defaultModel:
      input.defaultModel === undefined ? existing.defaultModel : input.defaultModel ? input.defaultModel : undefined,
  }
  if (input.apiKey === undefined) {
    // keep existing
  } else if (input.apiKey === null || input.apiKey === "") {
    next.apiKey = undefined
  } else {
    next.apiKey = input.apiKey
  }
  config.providers[id] = next
  await save(config)
  return next
}

export async function deleteProvider(id: string): Promise<void> {
  const config = await load()
  delete config.providers[id]
  if (config.active.provider === id) {
    config.active = { provider: null, model: null }
  }
  await save(config)
}

export async function getActive(): Promise<ActiveSelection> {
  const config = await load()
  return { ...config.active }
}

export async function setActive(selection: ActiveSelection): Promise<ActiveSelection> {
  const config = await load()
  config.active = { provider: selection.provider ?? null, model: selection.model ?? null }
  await save(config)
  return { ...config.active }
}

export async function exists(): Promise<boolean> {
  try {
    await stat(CONFIG_PATH)
    return true
  } catch {
    return false
  }
}
