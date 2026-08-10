import { rm, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { expect, test, beforeAll, afterAll } from "bun:test"
import { makeBridgeTestDirectory } from "./test-paths"

let dir: string

beforeAll(async () => {
  dir = await makeBridgeTestDirectory("appforge-cat-")
  process.env.APPFORGE_LLM_CONFIG_DIR = dir
  process.env.APPFORGE_MODELS_DEV_CACHE = join(dir, "models-dev.json")
})

afterAll(async () => {
  await rm(dir, { recursive: true, force: true })
})

test("fetchCatalog returns null when source is unreachable", async () => {
  process.env.APPFORGE_MODELS_DEV_URL = "http://127.0.0.1:1/api.json"
  const { fetchCatalog, _resetForTest: reset } = await import("../src/catalog")
  reset()
  const result = await fetchCatalog(true)
  expect(result).toBeNull()
})

test("fetchCatalog bounds the remote request with an abort signal", async () => {
  const originalFetch = globalThis.fetch
  let observedSignal: AbortSignal | null | undefined
  globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
    observedSignal = args[1]?.signal
    throw new Error("simulated offline catalog")
  }) as typeof fetch

  try {
    const { fetchCatalog, _resetForTest: reset } = await import("../src/catalog")
    reset()
    await fetchCatalog(true)
  } finally {
    globalThis.fetch = originalFetch
  }

  expect(observedSignal).toBeInstanceOf(AbortSignal)
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
