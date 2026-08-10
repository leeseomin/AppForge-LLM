import { randomUUID } from "node:crypto"
import { homedir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"
import { spawn } from "node:child_process"
import { chmod, lstat, mkdir, open, readFile, rename, stat, unlink } from "node:fs/promises"
import type { ActiveSelection, StoredProviderConfig } from "./types"

const KEYCHAIN_SERVICE = "appforge-llm"
const SECURITY_EXECUTABLE = "/usr/bin/security"
const MAX_SECURITY_OUTPUT_BYTES = 1024 * 1024
const DPAPI_STORE_FILE = "secrets.dpapi.json"
const DPAPI_POWERSHELL_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$request = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
$bytes = [Convert]::FromBase64String([string]$request.value)
$scope = [System.Security.Cryptography.DataProtectionScope]::CurrentUser
if ([string]$request.operation -eq 'protect') {
  $result = [System.Security.Cryptography.ProtectedData]::Protect($bytes, $null, $scope)
} elseif ([string]$request.operation -eq 'unprotect') {
  $result = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, $scope)
} else {
  throw 'Unsupported DPAPI operation'
}
[Console]::Out.Write([Convert]::ToBase64String($result))
`
type SecretKey = "apiKey"
type SecretBackend = "file" | "keychain" | "dpapi"
interface SecurityCommandResult { stdout: string }
type SecurityCommandRunner = (args: string[], input?: string) => Promise<SecurityCommandResult>
interface DpapiBlobFile { version: 1; secrets: Record<string, string> }

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

function normalizedProvider(value: unknown): StoredProviderConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {}
  const input = value as Record<string, unknown>
  const provider: StoredProviderConfig = {}
  if (typeof input.apiKey === "string" && input.apiKey.length > 0) provider.apiKey = input.apiKey
  if (typeof input.apiKeyRef === "string" && input.apiKeyRef.length > 0) provider.apiKeyRef = input.apiKeyRef
  if (typeof input.baseURL === "string" && input.baseURL.length > 0) provider.baseURL = input.baseURL
  if (typeof input.defaultModel === "string" && input.defaultModel.length > 0) {
    provider.defaultModel = input.defaultModel
  }
  return provider
}

function normalizedProviders(value: unknown): Record<string, StoredProviderConfig> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([id, provider]) => [id, normalizedProvider(provider)]),
  )
}

let cache: BridgeConfig | null = null
let loaded = false
let loadedPath: string | null = null
let loadedBackend: SecretBackend | null = null
let secretStoreOverride: SecretStore | null = null
let securityCommandRunnerOverride: SecurityCommandRunner | null = null
let dpapiCommandRunnerOverride: SecurityCommandRunner | null = null

function defaultConfigDir(): string {
  return resolve(
    process.env.APPFORGE_LLM_CONFIG_DIR || join(homedir(), ".appforge", "llm"),
  )
}

function currentConfigPath(): string {
  return resolve(process.env.APPFORGE_LLM_CONFIG || join(defaultConfigDir(), "providers.json"))
}

function currentDpapiStorePath(): string {
  return join(dirname(currentConfigPath()), DPAPI_STORE_FILE)
}

function currentSecretBackend(): SecretBackend {
  const platformDefault = process.platform === "darwin" ? "keychain" : process.platform === "win32" ? "dpapi" : "file"
  const raw = (process.env.APPFORGE_LLM_SECRET_BACKEND || platformDefault).trim().toLowerCase()
  if (raw === "file" || raw === "keychain" || raw === "dpapi") return raw
  throw new Error("APPFORGE_LLM_SECRET_BACKEND must be 'file', 'keychain', or 'dpapi'")
}

export function configPath(): string {
  return currentConfigPath()
}

export function secretBackend(): SecretBackend {
  return currentSecretBackend()
}

function assertOwnedByCurrentUser(path: string, uid: number): void {
  if (process.platform === "win32" || typeof process.getuid !== "function") return
  if (uid !== process.getuid()) throw new Error(`Refusing config path not owned by the current user: ${path}`)
}

async function ensureDir(): Promise<void> {
  const path = dirname(currentConfigPath())
  await mkdir(path, { recursive: true, mode: 0o700 })
  const info = await lstat(path)
  if (!info.isDirectory() || info.isSymbolicLink()) {
    throw new Error(`Refusing non-directory or symlinked config directory: ${path}`)
  }
  assertOwnedByCurrentUser(path, info.uid)
  if (process.platform !== "win32") {
    await chmod(path, 0o700)
    if ((await stat(path)).mode & 0o077) throw new Error(`Could not secure config directory: ${path}`)
  }
}

async function assertSafeConfigFile(path: string, repairPermissions: boolean): Promise<boolean> {
  let info
  try {
    info = await lstat(path)
  } catch (error) {
    if ((error as { code?: unknown }).code === "ENOENT") return false
    throw error
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error(`Refusing non-regular or symlinked config file: ${path}`)
  }
  assertOwnedByCurrentUser(path, info.uid)
  if (process.platform !== "win32" && (info.mode & 0o077) !== 0) {
    if (!repairPermissions) throw new Error(`Config file permissions are too broad: ${path}`)
    await chmod(path, 0o600)
    const secured = await lstat(path)
    if (!secured.isFile() || secured.isSymbolicLink() || (secured.mode & 0o077) !== 0) {
      throw new Error(`Could not secure config file: ${path}`)
    }
  }
  return true
}

async function atomicWritePrivate(path: string, payload: string): Promise<void> {
  await ensureDir()
  await assertSafeConfigFile(path, true)
  const temporary = join(dirname(path), `.${basename(path)}.${process.pid}.${randomUUID()}.tmp`)
  let handle: Awaited<ReturnType<typeof open>> | null = null
  try {
    handle = await open(temporary, "wx", 0o600)
    await handle.writeFile(payload, { encoding: "utf8" })
    await handle.sync()
    await handle.close()
    handle = null
    if (process.platform !== "win32") await chmod(temporary, 0o600)
    await rename(temporary, path)
    await assertSafeConfigFile(path, false)
    if (process.platform !== "win32") {
      const directory = await open(dirname(path), "r")
      try {
        await directory.sync()
      } finally {
        await directory.close()
      }
    }
  } finally {
    if (handle) await handle.close().catch(() => undefined)
    await unlink(temporary).catch((error) => {
      if ((error as { code?: unknown }).code !== "ENOENT") throw error
    })
  }
}

function keychainAccount(providerId: string, key: SecretKey): string {
  return `${providerId}:${key}`
}

function keychainRef(providerId: string, key: SecretKey): string {
  return `keychain:${KEYCHAIN_SERVICE}/${providerId}/${key}`
}

function dpapiRef(providerId: string, key: SecretKey): string {
  return `dpapi:${KEYCHAIN_SERVICE}/${providerId}/${key}`
}

function secretRef(backend: SecretBackend, providerId: string, key: SecretKey): string {
  if (backend === "keychain") return keychainRef(providerId, key)
  if (backend === "dpapi") return dpapiRef(providerId, key)
  throw new Error("The file backend does not use secret references")
}

function keychainInteractiveToken(value: string): string {
  if (!/^[A-Za-z0-9._:/-]+$/.test(value)) {
    throw new KeychainCommandError(null, "Invalid macOS Keychain item identifier")
  }
  return value
}

function isNotFoundError(error: unknown): boolean {
  const candidate = error as { code?: unknown; message?: unknown }
  const code = typeof candidate.code === "number" ? candidate.code : undefined
  const message = typeof candidate.message === "string" ? candidate.message : ""
  return code === 44 || /could not be found|not found/i.test(message)
}

class KeychainCommandError extends Error {
  constructor(readonly code: number | null, reason = "macOS Keychain command failed") {
    super(reason)
    this.name = "KeychainCommandError"
  }
}

class DpapiCommandError extends Error {
  constructor(readonly code: number | null, reason = "Windows DPAPI command failed") {
    super(reason)
    this.name = "DpapiCommandError"
  }
}

const runSecurityCommand: SecurityCommandRunner = (args, input) => new Promise((resolve, reject) => {
  const child = spawn(SECURITY_EXECUTABLE, args, { stdio: ["pipe", "pipe", "pipe"] })
  const stdout: Buffer[] = []
  let outputBytes = 0
  const timeout = setTimeout(() => {
    child.kill()
    reject(new KeychainCommandError(null, "macOS Keychain command timed out"))
  }, 30_000)
  child.stdout.on("data", (chunk: Buffer) => {
    outputBytes += chunk.length
    if (outputBytes <= MAX_SECURITY_OUTPUT_BYTES) stdout.push(chunk)
    else child.kill()
  })
  // Drain stderr, but never retain or re-emit it: native errors can include
  // sensitive command context.
  child.stderr.resume()
  child.once("error", () => {
    clearTimeout(timeout)
    reject(new KeychainCommandError(null))
  })
  child.once("close", (code) => {
    clearTimeout(timeout)
    if (outputBytes > MAX_SECURITY_OUTPUT_BYTES) {
      reject(new KeychainCommandError(code, "macOS Keychain output exceeded its limit"))
    } else if (code !== 0) {
      reject(new KeychainCommandError(code))
    } else {
      resolve({ stdout: Buffer.concat(stdout).toString("utf8") })
    }
  })
  child.stdin.end(input)
})

function securityCommand(args: string[], input?: string): Promise<SecurityCommandResult> {
  return (securityCommandRunnerOverride ?? runSecurityCommand)(args, input)
}

function dpapiPowerShellExecutable(): string {
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
  return join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
}

const runDpapiCommand: SecurityCommandRunner = (args, input) => new Promise((resolve, reject) => {
  const child = spawn(dpapiPowerShellExecutable(), args, {
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  })
  const stdout: Buffer[] = []
  let outputBytes = 0
  const timeout = setTimeout(() => {
    child.kill()
    reject(new DpapiCommandError(null, "Windows DPAPI command timed out"))
  }, 30_000)
  child.stdout.on("data", (chunk: Buffer) => {
    outputBytes += chunk.length
    if (outputBytes <= MAX_SECURITY_OUTPUT_BYTES) stdout.push(chunk)
    else child.kill()
  })
  child.stderr.resume()
  child.once("error", () => {
    clearTimeout(timeout)
    reject(new DpapiCommandError(null))
  })
  child.once("close", (code) => {
    clearTimeout(timeout)
    if (outputBytes > MAX_SECURITY_OUTPUT_BYTES) {
      reject(new DpapiCommandError(code, "Windows DPAPI output exceeded its limit"))
    } else if (code !== 0) {
      reject(new DpapiCommandError(code))
    } else {
      resolve({ stdout: Buffer.concat(stdout).toString("utf8") })
    }
  })
  child.stdin.end(input)
})

function dpapiCommand(args: string[], input?: string): Promise<SecurityCommandResult> {
  return (dpapiCommandRunnerOverride ?? runDpapiCommand)(args, input)
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
      const { stdout } = await securityCommand(
        ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", keychainAccount(providerId, key), "-w"],
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
    const service = keychainInteractiveToken(KEYCHAIN_SERVICE)
    const account = keychainInteractiveToken(keychainAccount(providerId, key))
    const encodedValue = Buffer.from(value, "utf8").toString("hex")
    await securityCommand(
      ["-q", "-i"],
      `add-generic-password -U -s ${service} -a ${account} -X ${encodedValue}\n`,
    )
    if (await this.get(providerId, key) !== value) {
      throw new KeychainCommandError(null, "macOS Keychain write could not be verified")
    }
  }

  async delete(providerId: string, key: SecretKey): Promise<void> {
    this.assertSupported()
    try {
      await securityCommand(
        ["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", keychainAccount(providerId, key)],
      )
    } catch (error) {
      if (!isNotFoundError(error)) throw error
    }
  }
}

function dpapiEntry(providerId: string, key: SecretKey): string {
  return `${providerId}:${key}`
}

function emptyDpapiBlobs(): DpapiBlobFile {
  return { version: 1, secrets: {} }
}

async function readDpapiBlobs(): Promise<DpapiBlobFile> {
  const path = currentDpapiStorePath()
  try {
    await assertSafeConfigFile(path, true)
    const parsed = JSON.parse(await readFile(path, "utf8")) as Partial<DpapiBlobFile>
    if (parsed.version !== 1 || !parsed.secrets || typeof parsed.secrets !== "object" || Array.isArray(parsed.secrets)) {
      throw new DpapiCommandError(null, "Windows DPAPI store has an invalid format")
    }
    const secrets: Record<string, string> = {}
    for (const [id, value] of Object.entries(parsed.secrets)) {
      if (typeof value !== "string" || value.length === 0) {
        throw new DpapiCommandError(null, "Windows DPAPI store contains an invalid ciphertext")
      }
      secrets[id] = value
    }
    return { version: 1, secrets }
  } catch (error) {
    if ((error as { code?: unknown }).code === "ENOENT") return emptyDpapiBlobs()
    if (error instanceof SyntaxError) {
      throw new DpapiCommandError(null, "Windows DPAPI store contains invalid JSON")
    }
    throw error
  }
}

async function writeDpapiBlobs(blobs: DpapiBlobFile): Promise<void> {
  await atomicWritePrivate(currentDpapiStorePath(), JSON.stringify(blobs, null, 2))
}

function normalizedBase64(value: string, label: string): string {
  const normalized = value.trim()
  if (!normalized || !/^[A-Za-z0-9+/]+={0,2}$/.test(normalized)) {
    throw new DpapiCommandError(null, `Windows DPAPI returned invalid ${label}`)
  }
  return normalized
}

class WindowsDpapiSecretStore implements SecretStore {
  private assertSupported(): void {
    if (process.platform !== "win32" && !dpapiCommandRunnerOverride) {
      throw new Error("APPFORGE_LLM_SECRET_BACKEND=dpapi is currently supported only on Windows")
    }
  }

  private async transform(operation: "protect" | "unprotect", value: string): Promise<string> {
    this.assertSupported()
    const args = [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      DPAPI_POWERSHELL_SCRIPT,
    ]
    const { stdout } = await dpapiCommand(args, JSON.stringify({ operation, value }))
    return normalizedBase64(stdout, `${operation} output`)
  }

  async get(providerId: string, key: SecretKey): Promise<string | undefined> {
    const blobs = await readDpapiBlobs()
    const encrypted = blobs.secrets[dpapiEntry(providerId, key)]
    if (!encrypted) return undefined
    const plaintext = await this.transform("unprotect", encrypted)
    return Buffer.from(plaintext, "base64").toString("utf8")
  }

  async set(providerId: string, key: SecretKey, value: string): Promise<void> {
    const plaintext = Buffer.from(value, "utf8").toString("base64")
    const encrypted = await this.transform("protect", plaintext)
    const blobs = await readDpapiBlobs()
    blobs.secrets[dpapiEntry(providerId, key)] = encrypted
    await writeDpapiBlobs(blobs)
    if (await this.get(providerId, key) !== value) {
      throw new DpapiCommandError(null, "Windows DPAPI write could not be verified")
    }
  }

  async delete(providerId: string, key: SecretKey): Promise<void> {
    const blobs = await readDpapiBlobs()
    const id = dpapiEntry(providerId, key)
    if (!(id in blobs.secrets)) return
    delete blobs.secrets[id]
    await writeDpapiBlobs(blobs)
  }
}

function selectedSecretStore(): SecretStore {
  if (secretStoreOverride) return secretStoreOverride
  const backend = currentSecretBackend()
  if (backend === "keychain") return new MacOSKeychainSecretStore()
  if (backend === "dpapi") return new WindowsDpapiSecretStore()
  throw new Error("The file secret backend does not provide a SecretStore")
}

async function hydrateSecrets(config: BridgeConfig): Promise<BridgeConfig> {
  if (currentSecretBackend() === "file") return config
  const store = selectedSecretStore()
  const providers: Record<string, StoredProviderConfig> = {}
  for (const [id, provider] of Object.entries(config.providers)) {
    const next = normalizedProvider(provider)
    if (next.apiKeyRef && !next.apiKey) {
      next.apiKey = await store.get(id, "apiKey")
    }
    providers[id] = next
  }
  return { providers, active: { ...config.active } }
}

async function serializeForStorage(config: BridgeConfig): Promise<BridgeConfig> {
  const backend = currentSecretBackend()
  if (backend === "file") {
    const providers: Record<string, StoredProviderConfig> = {}
    for (const [id, provider] of Object.entries(config.providers)) {
      const next = normalizedProvider(provider)
      delete next.apiKeyRef
      providers[id] = next
    }
    return { providers, active: { ...config.active } }
  }

  const store = selectedSecretStore()
  const providers: Record<string, StoredProviderConfig> = {}
  for (const [id, provider] of Object.entries(config.providers)) {
    const next = normalizedProvider(provider)
    if (next.apiKey && next.apiKey.length > 0) {
      await store.set(id, "apiKey", next.apiKey)
      next.apiKeyRef = secretRef(backend, id, "apiKey")
    }
    delete next.apiKey
    providers[id] = next
  }
  return { providers, active: { ...config.active } }
}

export async function load(): Promise<BridgeConfig> {
  const path = currentConfigPath()
  const backend = currentSecretBackend()
  if (loaded && cache && loadedPath === path && loadedBackend === backend) return cache
  try {
    await assertSafeConfigFile(path, true)
    const raw = await readFile(path, "utf8")
    const parsed = JSON.parse(raw) as Partial<BridgeConfig>
    cache = await hydrateSecrets({
      providers: normalizedProviders(parsed.providers),
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
  if (
    backend !== "file"
    && Object.values(cache.providers).some((provider) =>
      Boolean(provider.apiKey && !provider.apiKeyRef),
    )
  ) {
    await save(cache)
  }
  return cache
}

export async function save(config: BridgeConfig): Promise<void> {
  const stored = await serializeForStorage(config)
  const payload = JSON.stringify(stored, null, 2)
  await atomicWritePrivate(currentConfigPath(), payload)
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
  clearApiKey?: boolean
  baseURL?: string | null
  defaultModel?: string | null
}): Promise<StoredProviderConfig> {
  const current = await load()
  const config: BridgeConfig = {
    providers: { ...current.providers },
    active: { ...current.active },
  }
  const existing = current.providers[id] ?? {}
  if (input.clearApiKey && input.apiKey) {
    throw new Error("clearApiKey cannot be combined with apiKey")
  }
  const next: StoredProviderConfig = {
    ...existing,
    baseURL: input.baseURL === undefined ? existing.baseURL : input.baseURL ? input.baseURL : undefined,
    defaultModel:
      input.defaultModel === undefined ? existing.defaultModel : input.defaultModel ? input.defaultModel : undefined,
  }
  if (input.clearApiKey) {
    if (currentSecretBackend() !== "file") {
      await selectedSecretStore().delete(id, "apiKey")
    }
    next.apiKey = undefined
    next.apiKeyRef = undefined
  } else if (input.apiKey === undefined || input.apiKey === null || input.apiKey === "") {
    // keep existing
  } else {
    next.apiKey = input.apiKey
  }
  config.providers[id] = next
  await save(config)
  return next
}

export async function deleteProvider(id: string): Promise<void> {
  const config = await load()
  if (currentSecretBackend() !== "file") {
    await selectedSecretStore().delete(id, "apiKey")
  }
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
    await stat(currentConfigPath())
    return true
  } catch {
    return false
  }
}

export function _setSecretStoreForTest(store: SecretStore | null): void {
  secretStoreOverride = store
}

export function _setSecurityCommandRunnerForTest(runner: SecurityCommandRunner | null): void {
  securityCommandRunnerOverride = runner
}

export function _setDpapiCommandRunnerForTest(runner: SecurityCommandRunner | null): void {
  dpapiCommandRunnerOverride = runner
}

export function _resetForTest(): void {
  cache = null
  loaded = false
  loadedPath = null
  loadedBackend = null
  secretStoreOverride = null
  securityCommandRunnerOverride = null
  dpapiCommandRunnerOverride = null
}
