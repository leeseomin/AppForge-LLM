import { mkdtemp, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { expect, test, beforeAll } from "bun:test"
import { get, statusOf, list, _resetForTest as resetRegistry } from "../src/registry"
import { _resetForTest as resetCatalog } from "../src/catalog"

let tmp: string

const REMOVED_PROVIDER_IDS = [
  "groq",
  "cerebras",
  "togetherai",
  "fireworks",
  "deepinfra",
  "github-copilot",
]

beforeAll(async () => {
  tmp = await mkdtemp(join(tmpdir(), "appforge-reg-"))
  process.env.APPFORGE_LLM_CONFIG_DIR = tmp
  process.env.APPFORGE_MODELS_DEV_URL = "http://127.0.0.1:1/api.json"
  process.env.APPFORGE_MODELS_DEV_CACHE = join(tmp, "models-dev.json")
  resetCatalog()
  resetRegistry()
})

test("static fallback lists known providers", async () => {
  const entries = await list()
  const ids = entries.map((e) => e.id)
  expect(ids).toContain("deepseek")
  expect(ids).toContain("openai")
  expect(ids).toContain("anthropic")
})

test("removed providers are not exposed by the registry", async () => {
  const ids = (await list()).map((entry) => entry.id)

  for (const providerId of REMOVED_PROVIDER_IDS) {
    expect(ids).not.toContain(providerId)
  }
})

test("stored OAuth credentials are ignored for providers without OAuth support", async () => {
  const entry = await get("xai")
  expect(entry).toBeDefined()
  if (!entry) return

  const status = statusOf(entry, {
    oauth: {
      type: "oauth",
      access: "removed-xai-oauth-token",
      refresh: "removed-xai-refresh-token",
      expires: 0,
    },
  })

  expect(status.configured).toBe(false)
  expect(status.key_source).toBe("none")
  expect(status.oauth).toBe(false)
})

test("deepseek defaults to v4 pro and keeps it selectable", async () => {
  const entry = await get("deepseek")
  expect(entry).toBeDefined()
  if (!entry) return

  const status = statusOf(entry, undefined)
  expect(status.default_model).toBe("deepseek-v4-pro")
  expect(status.model_count).toBeGreaterThan(0)
  expect(status.models).toEqual([])

  const statusWithModels = statusOf(entry, undefined, { includeModels: true })
  expect((statusWithModels.models ?? []).map((model) => model.id)).toContain("deepseek-v4-pro")
})

test("deepseek legacy defaults are normalized to v4 pro", async () => {
  const entry = await get("deepseek")
  expect(entry).toBeDefined()
  if (!entry) return

  expect(statusOf(entry, { defaultModel: "deepseek-chat" }).default_model).toBe("deepseek-v4-pro")
  expect(statusOf(entry, { defaultModel: "deepseek-reasoner" }).default_model).toBe("deepseek-v4-pro")
})

test("remote catalog metadata cannot replace local endpoint or environment security definitions", async () => {
  await writeFile(
    join(tmp, "models-dev.json"),
    JSON.stringify({
      openai: {
        id: "openai",
        name: "OpenAI catalog label",
        env: ["ATTACKER_SELECTED_ENV"],
        api: "https://attacker.example/v1",
        npm: "@ai-sdk/openai",
        models: { "catalog-model": { id: "catalog-model", name: "Catalog model" } },
      },
      attacker: {
        id: "attacker",
        name: "Injected provider",
        env: ["HOME"],
        api: "https://attacker.example",
        npm: "@ai-sdk/openai-compatible",
        models: {},
      },
    }),
    "utf8",
  )
  resetCatalog()
  resetRegistry()

  const entries = await list()
  const openai = entries.find((entry) => entry.id === "openai")
  expect(openai?.env_key).toBe("OPENAI_API_KEY")
  expect(openai?.base_url_default).not.toBe("https://attacker.example/v1")
  expect(openai?.models.map((model) => model.id)).toContain("catalog-model")
  expect(entries.some((entry) => entry.id === "attacker")).toBe(false)
})
