import { mkdtemp } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { expect, test } from "bun:test"
import { _resetForTest as resetRegistry } from "../src/registry"
import { _resetForTest as resetCatalog } from "../src/catalog"

async function createIsolatedApp(prefix: string) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_CONFIG_DIR = dir
  process.env.APPFORGE_MODELS_DEV_URL = "http://127.0.0.1:1/api.json"
  process.env.APPFORGE_MODELS_DEV_CACHE = join(dir, "models-dev.json")
  resetCatalog()
  resetRegistry()
  const { createApp } = await import("../src/server")
  return createApp()
}

test("provider upsert response does not echo stored api key", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-")
  const fakeKey = `sk-${"test-secret-value"}`

  const response = await app.fetch(
    new Request("http://127.0.0.1/providers/openai", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        apiKey: fakeKey,
        defaultModel: "gpt-4o-mini",
      }),
    }),
  )

  expect(response.status).toBe(200)
  expect(response.headers.get("access-control-allow-origin")).not.toBe("*")
  const raw = await response.text()
  expect(raw).not.toContain(fakeKey)
  expect(raw).not.toContain("apiKey")
  const payload = JSON.parse(raw)
  expect(payload.status.has_key).toBe(true)
  expect(payload.status.key_source).toBe("stored")
})

test("provider list omits model payload unless include_models is requested", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-providers-")

  const compactResponse = await app.fetch(new Request("http://127.0.0.1/providers"))
  expect(compactResponse.status).toBe(200)
  const compactPayload = await compactResponse.json()
  const compactOpenAI = compactPayload.providers.find((provider: { id: string }) => provider.id === "openai")
  expect(compactOpenAI.models).toEqual([])
  expect(compactOpenAI.model_count).toBeGreaterThan(0)

  const fullResponse = await app.fetch(new Request("http://127.0.0.1/providers?include_models=true"))
  expect(fullResponse.status).toBe(200)
  const fullPayload = await fullResponse.json()
  const fullOpenAI = fullPayload.providers.find((provider: { id: string }) => provider.id === "openai")
  expect(fullOpenAI.models.map((model: { id: string }) => model.id)).toContain("gpt-4o-mini")
})

test("all-provider model catalog endpoint is not exposed", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-no-all-models-")

  const response = await app.fetch(new Request("http://127.0.0.1/providers/models"))

  expect(response.status).toBe(404)
})
