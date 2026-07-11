import { mkdtemp } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { expect, test, beforeAll } from "bun:test"
import { get, statusOf, list, _resetForTest as resetRegistry } from "../src/registry"
import { _resetForTest as resetCatalog } from "../src/catalog"

let tmp: string

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
