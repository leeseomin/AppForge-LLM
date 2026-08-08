import { lstat, mkdtemp, readFile, stat, symlink, writeFile } from "node:fs/promises"
import { isAbsolute, join } from "node:path"
import { tmpdir } from "node:os"
import { afterEach, expect, test } from "bun:test"
import {
  _resetForTest,
  _setSecurityCommandRunnerForTest,
  _setSecretStoreForTest,
  configPath,
  secretBackend,
  setProvider,
  getProvider,
  type SecretStore,
} from "../src/config"

afterEach(() => {
  delete process.env.APPFORGE_LLM_CONFIG
  delete process.env.APPFORGE_LLM_CONFIG_DIR
  delete process.env.APPFORGE_DATA_DIR
  delete process.env.APPFORGE_LLM_SECRET_BACKEND
  _resetForTest()
})

test("macOS Keychain is the secure default and other platforms retain the file backend", () => {
  delete process.env.APPFORGE_LLM_SECRET_BACKEND

  expect(secretBackend()).toBe(process.platform === "darwin" ? "keychain" : "file")
})

test("secret config paths are absolute and independent of web job data", () => {
  process.env.APPFORGE_LLM_CONFIG_DIR = "relative-llm-config"
  expect(isAbsolute(configPath())).toBeTrue()

  delete process.env.APPFORGE_LLM_CONFIG_DIR
  process.env.APPFORGE_DATA_DIR = join(tmpdir(), "web-job-state-must-not-own-secrets")
  expect(configPath().startsWith(process.env.APPFORGE_DATA_DIR)).toBeFalse()
})

test("file backend creates a private directory and atomic 0600 config file", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-file-perms-"))
  const configDir = join(dir, "private")
  process.env.APPFORGE_LLM_CONFIG_DIR = configDir
  process.env.APPFORGE_LLM_CONFIG = join(configDir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-file-secret" })

  if (process.platform !== "win32") {
    expect((await stat(configDir)).mode & 0o077).toBe(0)
    expect((await stat(configPath())).mode & 0o077).toBe(0)
  }
})

test("file backend refuses to follow a symlinked config file", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-symlink-"))
  const target = join(dir, "target.json")
  const link = join(dir, "providers.json")
  await writeFile(target, "do-not-overwrite", "utf8")
  await symlink(target, link)
  process.env.APPFORGE_LLM_CONFIG = link
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await expect(setProvider("openai", { apiKey: "sk-file-secret" })).rejects.toThrow()
  expect(await readFile(target, "utf8")).toBe("do-not-overwrite")
  expect((await lstat(link)).isSymbolicLink()).toBe(true)
})

test("keychain backend stores provider secrets as JSON references", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-config-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  const secrets = new Map<string, string>()
  const store: SecretStore = {
    async get(providerId, key) {
      return secrets.get(`${providerId}:${key}`)
    },
    async set(providerId, key, value) {
      secrets.set(`${providerId}:${key}`, value)
    },
    async delete(providerId, key) {
      secrets.delete(`${providerId}:${key}`)
    },
  }
  _resetForTest()
  _setSecretStoreForTest(store)

  await setProvider("openai", {
    apiKey: "sk-test-secret",
    baseURL: "https://example.test/v1",
    defaultModel: "gpt-4o-mini",
  })

  const raw = await readFile(configPath(), "utf8")
  const payload = JSON.parse(raw)
  expect(raw).not.toContain("sk-test-secret")
  expect(payload.providers.openai.apiKey).toBeUndefined()
  expect(payload.providers.openai.apiKeyRef).toBe("keychain:appforge-llm/openai/apiKey")
  expect(secrets.get("openai:apiKey")).toBe("sk-test-secret")
  expect((await getProvider("openai"))?.apiKey).toBe("sk-test-secret")
})

test("keychain command receives the secret on stdin instead of argv", async () => {
  if (process.platform !== "darwin") return
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-keychain-stdin-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  const secret = ["sk", "never", "in", "process", "arguments"].join("-")
  const calls: Array<{ args: string[]; input?: string }> = []
  _resetForTest()
  _setSecurityCommandRunnerForTest(async (args, input) => {
    calls.push({ args: [...args], input })
    if (args[0] === "find-generic-password") return { stdout: `${secret}\n` }
    return { stdout: "" }
  })

  await setProvider("openai", { apiKey: secret })

  const add = calls.find((call) => call.args[0] === "add-generic-password")
  expect(add).toBeDefined()
  expect(add?.args).not.toContain(secret)
  expect(add?.args.at(-1)).toBe("-w")
  expect(add?.input).toBe(`${secret}\n`)
})

test("omitted or null keys preserve the credential and explicit clearing removes it", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-key-semantics-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-existing" })
  await setProvider("openai", { defaultModel: "gpt-4o-mini" })
  expect((await getProvider("openai"))?.apiKey).toBe("sk-existing")
  await setProvider("openai", { apiKey: null })
  expect((await getProvider("openai"))?.apiKey).toBe("sk-existing")
  await setProvider("openai", { clearApiKey: true })
  expect((await getProvider("openai"))?.apiKey).toBeUndefined()
})
