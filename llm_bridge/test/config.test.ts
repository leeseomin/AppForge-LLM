import { lstat, mkdtemp, readFile, stat, symlink, writeFile } from "node:fs/promises"
import { isAbsolute, join } from "node:path"
import { tmpdir } from "node:os"
import { afterEach, expect, test } from "bun:test"
import {
  _resetForTest,
  _setDpapiCommandRunnerForTest,
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

test("macOS Keychain and Windows DPAPI are the secure platform defaults", () => {
  delete process.env.APPFORGE_LLM_SECRET_BACKEND

  const expected = process.platform === "darwin" ? "keychain" : process.platform === "win32" ? "dpapi" : "file"
  expect(secretBackend()).toBe(expected)
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
  try {
    await symlink(target, link, "file")
  } catch (error) {
    const code = (error as { code?: unknown }).code
    if (process.platform === "win32" && (code === "EPERM" || code === "EACCES")) return
    throw error
  }
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

test("dpapi backend stores only a reference in provider JSON", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-dpapi-config-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
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

  await setProvider("deepseek", { apiKey: "sk-dpapi-secret", defaultModel: "deepseek-chat" })

  const raw = await readFile(configPath(), "utf8")
  const payload = JSON.parse(raw)
  expect(raw).not.toContain("sk-dpapi-secret")
  expect(payload.providers.deepseek.apiKey).toBeUndefined()
  expect(payload.providers.deepseek.apiKeyRef).toBe("dpapi:appforge-llm/deepseek/apiKey")
  expect((await getProvider("deepseek"))?.apiKey).toBe("sk-dpapi-secret")
})

test("dpapi command receives secret material only through stdin", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-dpapi-stdin-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  const secret = "sk-never-in-process-arguments"
  const secretBase64 = Buffer.from(secret, "utf8").toString("base64")
  const encrypted = Buffer.from("opaque-ciphertext", "utf8").toString("base64")
  const calls: Array<{ args: string[]; input?: string }> = []
  _resetForTest()
  _setDpapiCommandRunnerForTest(async (args, input) => {
    calls.push({ args: [...args], input })
    const request = JSON.parse(input || "{}")
    return { stdout: request.operation === "protect" ? encrypted : secretBase64 }
  })

  await setProvider("openrouter", { apiKey: secret })

  expect(calls.length).toBeGreaterThanOrEqual(2)
  for (const call of calls) {
    expect(call.args.join(" ")).not.toContain(secret)
    expect(call.args.join(" ")).not.toContain(secretBase64)
  }
  expect(calls.some((call) => call.input?.includes(secretBase64))).toBe(true)
})

test("plaintext Windows config is migrated into the secure backend on load", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-dpapi-migrate-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  await writeFile(
    configPath(),
    JSON.stringify({
      providers: { openai: { apiKey: "legacy-plaintext", defaultModel: "gpt-4.1-mini" } },
      active: { provider: "openai", model: "gpt-4.1-mini" },
    }),
    "utf8",
  )
  const secrets = new Map<string, string>()
  _resetForTest()
  _setSecretStoreForTest({
    async get(providerId, key) { return secrets.get(`${providerId}:${key}`) },
    async set(providerId, key, value) { secrets.set(`${providerId}:${key}`, value) },
    async delete(providerId, key) { secrets.delete(`${providerId}:${key}`) },
  })

  expect((await getProvider("openai"))?.apiKey).toBe("legacy-plaintext")
  const raw = await readFile(configPath(), "utf8")
  expect(raw).not.toContain("legacy-plaintext")
  expect(JSON.parse(raw).providers.openai.apiKeyRef).toBe("dpapi:appforge-llm/openai/apiKey")
})

test("keychain command writes through interactive stdin without exposing the secret in argv", async () => {
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

  const add = calls.find((call) => call.args.includes("-i"))
  expect(add).toBeDefined()
  expect(add?.args).not.toContain(secret)
  expect(add?.args).toEqual(["-q", "-i"])
  expect(add?.input).toBe(
    `add-generic-password -U -s appforge-llm -a openai:apiKey -X ${Buffer.from(secret, "utf8").toString("hex")}\n`,
  )
  expect(add?.input).not.toContain(secret)
})

test("keychain backend rejects a write that cannot be read back", async () => {
  if (process.platform !== "darwin") return
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-keychain-readback-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  _resetForTest()
  _setSecurityCommandRunnerForTest(async (args) => {
    if (args[0] === "find-generic-password") return { stdout: "" }
    return { stdout: "" }
  })

  await expect(setProvider("deepseek", { apiKey: "sk-readback-must-match" })).rejects.toThrow(
    "could not be verified",
  )
  expect((await getProvider("deepseek"))?.apiKey).toBeUndefined()
  await expect(readFile(configPath(), "utf8")).rejects.toThrow()
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

test("legacy OAuth fields are discarded while API-key settings are preserved", async () => {
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-legacy-oauth-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  await writeFile(
    configPath(),
    JSON.stringify({
      providers: {
        openai: {
          apiKey: "sk-existing",
          defaultModel: "gpt-4o-mini",
          oauth: {
            type: "oauth",
            access: "legacy-access-token",
            refresh: "legacy-refresh-token",
            expires: 1_900_000_000_000,
          },
          oauthRef: "keychain:appforge-llm/openai/oauth",
        },
      },
      active: { provider: "openai", model: "gpt-4o-mini" },
    }),
    "utf8",
  )
  _resetForTest()

  const provider = await getProvider("openai")
  expect((provider as unknown as Record<string, unknown>).oauth).toBeUndefined()
  expect((provider as unknown as Record<string, unknown>).oauthRef).toBeUndefined()
  expect(provider?.apiKey).toBe("sk-existing")

  await setProvider("openai", { defaultModel: "gpt-4.1-mini" })
  const raw = await readFile(configPath(), "utf8")
  expect(raw.toLowerCase()).not.toContain("oauth")
  expect(JSON.parse(raw).providers.openai.apiKey).toBe("sk-existing")
})

test("Windows DPAPI backend performs a real CurrentUser round trip", async () => {
  if (process.platform !== "win32") return
  const dir = await mkdtemp(join(tmpdir(), "appforge-bridge-dpapi-real-"))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-windows-dpapi-roundtrip" })

  expect((await getProvider("openai"))?.apiKey).toBe("sk-windows-dpapi-roundtrip")
  expect(await readFile(configPath(), "utf8")).not.toContain("sk-windows-dpapi-roundtrip")
  expect(await readFile(join(dir, "secrets.dpapi.json"), "utf8")).not.toContain(
    "sk-windows-dpapi-roundtrip",
  )
})
