import { mkdtemp, readFile } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { afterEach, expect, test } from "bun:test"
import {
  _resetForTest,
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
  delete process.env.APPFORGE_LLM_SECRET_BACKEND
  _resetForTest()
})

test("file secret backend remains the default", () => {
  delete process.env.APPFORGE_LLM_SECRET_BACKEND

  expect(secretBackend()).toBe("file")
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
