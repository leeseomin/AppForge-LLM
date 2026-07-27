import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { mkdir, readFile, stat, writeFile } from "node:fs/promises"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import type { ActiveSelection, OAuthCredential, StoredProviderConfig } from "./types"

const execFileAsync = promisify(execFile)
const KEYCHAIN_SERVICE = "appforge-llm"
type SecretKey = "apiKey" | "oauth"
type SecretBackend = "file" | "keychain"

export interface SecretStore {
  get(providerId: string, key: SecretKey): Promise<string | undefined>
  set(providerId: string, key: SecretKey, value: string): Promise<void>
  delete(providerId: string, key: SecretKey): Promise<void>
}

export interface BridgeConfig {
  providers: Record<string, StoredProviderConfig>
  active: ActiveSelection
}

const EMPTY: BridgeConfig = { providers: {}, active: { provider: null, model: null } }

let cache: BridgeConfig | null = null
let loaded = false
let loadedPath: string | null = null
let loadedBackend: SecretBackend | null = null
let secretStoreOverride: SecretStore | null = null

function defaultConfigDir(): string {
  return process.env.APPFORGE_LLM_CONFIG_DIR ||
    process.env.APPFORGE_DATA_DIR ||
    join(homedir(), ".appforge", "llm")
}

function currentConfigPath(): string {
  return process.env.APPFORGE_LLM_CONFIG || join(defaultConfigDir(), "providers.json")
}

function currentSecretBackend(): SecretBackend {
  const raw = (process.env.APPFORGE_LLM_SECRET_BACKEND || "file").trim().toLowerCase()
  if (raw === "file" || raw === "keychain") return raw
  throw new Error("APPFORGE_LLM_SECRET_BACKEND must be either 'file' or 'keychain'")
}

export function configPath(): string {
  return currentConfigPath()
}

export function secretBackend(): SecretBackend {
  return currentSecretBackend()
}

async function ensureDir(): Promise<void> {
  await mkdir(dirname(currentConfigPath()), { recursive: true })
}

async function applyPerms(): Promise<void> {
  try {
    await chmod0600(currentConfigPath())
  } catch {
    // Permissions are best-effort on some filesystems; never block writes.
  }
}

async function chmod0600(path: string): Promise<void> {
  const { chmod } = await import("node:fs/promises")
  await chmod(path, 0o600)
}

function keychainAccount(providerId: string, key: SecretKey): string {
  return `${providerId}:${key}`
}

function keychainRef(providerId: string, key: SecretKey): string {
  return `keychain:${KEYCHAIN_SERVICE}/${providerId}/${key}`
}

function isNotFoundError(error: unknown): boolean {
  const candidate = error as { code?: unknown; stderr?: unknown; message?: unknown }
  const code = typeof candidate.code === "number" ? candidate.code : undefined
  const stderr = typeof candidate.stderr === "string" ? candidate.stderr : ""
  const message = typeof candidate.message === "string" ? candidate.message : ""
  return code === 44 || /could not be found|not found/i.test(`${stderr}\n${message}`)
}

class MacOSKeychainSecretStore implements SecretStore {
  private assertSupported(): void {
    if (process.platform !== "darwin") {
      throw new Error("APPFORGE_LLM_SECRET_BACKEND=keychain is currently supported only on macOS")
    }
  }

  async get(providerId: string, key: SecretKey): Promise<string | undefined> {
    this.assertSupported()
    try {
      const { stdout } = await execFileAsync(
        "security",
        ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", keychainAccount(providerId, key), "-w"],
        { maxBuffer: 1024 * 1024 },
      )
      const value = String(stdout).replace(/\r?\n$/, "")
      return value.length > 0 ? value : undefined
    } catch (error) {
      if (isNotFoundError(error)) return undefined
      throw error
    }
  }

  async set(providerId: string, key: SecretKey, value: string): Promise<void> {
    this.assertSupported()
    await execFileAsync(
      "security",
      ["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", keychainAccount(providerId, key), "-w", value],
      { maxBuffer: 1024 * 1024 },
    )
  }

  async delete(providerId: string, key: SecretKey): Promise<void> {
    this.assertSupported()
    try {
      await execFileAsync(
        "security",
        ["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", keychainAccount(providerId, key)],
        { maxBuffer: 1024 * 1024 },
      )
    } catch (error) {
      if (!isNotFoundError(error)) throw error
    }
  }
}

function selectedSecretStore(): SecretStore {
  if (secretStoreOverride) return secretStoreOverride
  return new MacOSKeychainSecretStore()
}

async function hydrateSecrets(config: BridgeConfig): Promise<BridgeConfig> {
  if (currentSecretBackend() === "file") return config
  const store = selectedSecretStore()
  const providers: Record<string, StoredProviderConfig> = {}
  for (const [id, provider] of Object.entries(config.providers)) {
    const next: StoredProviderConfig = { ...provider }
    if (next.apiKeyRef && !next.apiKey) {
      next.apiKey = await store.get(id, "apiKey")
    }
    if (next.oauthRef && !next.oauth) {
      const raw = await store.get(id, "oauth")
      if (raw) next.oauth = JSON.parse(raw) as OAuthCredential
    }
    providers[id] = next
  }
  return { providers, active: { ...config.active } }
}

async function serializeForStorage(config: BridgeConfig): Promise<BridgeConfig> {
  if (currentSecretBackend() === "file") {
    const providers: Record<string, StoredProviderConfig> = {}
    for (const [id, provider] of Object.entries(config.providers)) {
      const next: StoredProviderConfig = { ...provider }
      delete next.apiKeyRef
      delete next.oauthRef
      providers[id] = next
    }
    return { providers, active: { ...config.active } }
  }

  const store = selectedSecretStore()
  const providers: Record<string, StoredProviderConfig> = {}
  for (const [id, provider] of Object.entries(config.providers)) {
    const next: StoredProviderConfig = { ...provider }
    if (next.apiKey && next.apiKey.length > 0) {
      await store.set(id, "apiKey", next.apiKey)
      next.apiKeyRef = keychainRef(id, "apiKey")
    }
    if (next.oauth) {
      await store.set(id, "oauth", JSON.stringify(next.oauth))
      next.oauthRef = keychainRef(id, "oauth")
    }
    delete next.apiKey
    delete next.oauth
    providers[id] = next
  }
  return { providers, active: { ...config.active } }
}

export async function load(): Promise<BridgeConfig> {
  const path = currentConfigPath()
  const backend = currentSecretBackend()
  if (loaded && cache && loadedPath === path && loadedBackend === backend) return cache
  try {
    const raw = await readFile(path, "utf8")
    const parsed = JSON.parse(raw) as Partial<BridgeConfig>
    cache = await hydrateSecrets({
      providers: parsed.providers && typeof parsed.providers === "object" ? (parsed.providers as BridgeConfig["providers"]) : {},
      active: {
        provider: parsed.active?.provider ?? null,
        model: parsed.active?.model ?? null,
      },
    })
  } catch (error) {
    if (error instanceof SyntaxError) {
      cache = { providers: { ...EMPTY.providers }, active: { ...EMPTY.active } }
    } else if ((error as { code?: unknown }).code === "ENOENT") {
      cache = { providers: { ...EMPTY.providers }, active: { ...EMPTY.active } }
    } else {
      throw error
    }
  }
  if (!cache) {
    cache = { providers: { ...EMPTY.providers }, active: { ...EMPTY.active } }
  }
  loaded = true
  loadedPath = path
  loadedBackend = backend
  return cache
}

export async function save(config: BridgeConfig): Promise<void> {
  await ensureDir()
  const stored = await serializeForStorage(config)
  const payload = JSON.stringify(stored, null, 2)
  await writeFile(currentConfigPath(), payload, "utf8")
  await applyPerms()
  cache = await hydrateSecrets(stored)
  loaded = true
  loadedPath = currentConfigPath()
  loadedBackend = currentSecretBackend()
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
    if (currentSecretBackend() === "keychain") {
      await selectedSecretStore().delete(id, "apiKey")
    }
    next.apiKey = undefined
    next.apiKeyRef = undefined
  } else {
    next.apiKey = input.apiKey
  }
  config.providers[id] = next
  await save(config)
  return next
}

export async function deleteProvider(id: string): Promise<void> {
  const config = await load()
  if (currentSecretBackend() === "keychain") {
    await selectedSecretStore().delete(id, "apiKey")
    await selectedSecretStore().delete(id, "oauth")
  }
  delete config.providers[id]
  if (config.active.provider === id) {
    config.active = { provider: null, model: null }
  }
  await save(config)
}

export async function setOAuthCredential(id: string, credential: OAuthCredential): Promise<void> {
  const config = await load()
  const existing = config.providers[id] ?? {}
  config.providers[id] = { ...existing, oauth: credential }
  await save(config)
}

export async function getOAuthCredential(id: string): Promise<OAuthCredential | undefined> {
  const config = await load()
  return config.providers[id]?.oauth
}

export async function deleteOAuthCredential(id: string): Promise<void> {
  const config = await load()
  const existing = config.providers[id]
  if (!existing) return
  if (currentSecretBackend() === "keychain") {
    await selectedSecretStore().delete(id, "oauth")
  }
  delete existing.oauth
  delete existing.oauthRef
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
    await stat(currentConfigPath())
    return true
  } catch {
    return false
  }
}

export function _setSecretStoreForTest(store: SecretStore | null): void {
  secretStoreOverride = store
}

export function _resetForTest(): void {
  cache = null
  loaded = false
  loadedPath = null
  loadedBackend = null
  secretStoreOverride = null
}
