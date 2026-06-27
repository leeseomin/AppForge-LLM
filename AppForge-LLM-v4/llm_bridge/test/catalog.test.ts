import { mkdtemp, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { expect, test, beforeAll } from "bun:test"

let dir: string

beforeAll(async () => {
  dir = await mkdtemp(join(tmpdir(), "appforge-cat-"))
  process.env.APPFORGE_LLM_CONFIG_DIR = dir
  process.env.APPFORGE_MODELS_DEV_CACHE = join(dir, "models-dev.json")
})

test("fetchCatalog returns null when source is unreachable", async () => {
  process.env.APPFORGE_MODELS_DEV_URL = "http://127.0.0.1:1/api.json"
  const { fetchCatalog, _resetForTest: reset } = await import("../src/catalog")
  reset()
  const result = await fetchCatalog(true)
  expect(result).toBeNull()
})

test("fetchCatalog reads a stale cache file when network fails", async () => {
  const cached = {
    deepseek: {
      id: "deepseek",
      name: "DeepSeek",
      env: ["DEEPSEEK_API_KEY"],
      api: "https://api.deepseek.com",
      npm: "@ai-sdk/openai-compatible",
      models: {
        "deepseek-v4-pro": { id: "deepseek-v4-pro", name: "DeepSeek V4 Pro" },
      },
    },
  }
  await writeFile(join(dir, "models-dev.json"), JSON.stringify(cached))
  process.env.APPFORGE_MODELS_DEV_URL = "http://127.0.0.1:1/api.json"
  const { fetchCatalog, _resetForTest: reset } = await import("../src/catalog")
  reset()
  const result = await fetchCatalog(true)
  expect(result).not.toBeNull()
  expect(result?.deepseek?.name).toBe("DeepSeek")
})
