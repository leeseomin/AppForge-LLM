import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { mkdir, readFile, stat, writeFile } from "node:fs/promises"
import { VERSION } from "./version"

function defaultConfigDir(): string {
  return (
    process.env.APPFORGE_LLM_CONFIG_DIR ||
    process.env.APPFORGE_DATA_DIR ||
    join(homedir(), ".appforge", "llm")
  )
}
function cachePath(): string {
  return process.env.APPFORGE_MODELS_DEV_CACHE || join(defaultConfigDir(), "models-dev.json")
}
function sourceUrl(): string {
  return process.env.APPFORGE_MODELS_DEV_URL || "https://models.dev"
}
const TTL_MS = 5 * 60 * 1000
const USER_AGENT = `appforge-llm-bridge/${VERSION}`

export interface CatalogModel {
  id: string
  name: string
  tool_call?: boolean
  reasoning?: boolean
  attachment?: boolean
}

export interface CatalogProvider {
  id: string
  name: string
  env: string[]
  api?: string
  npm?: string
  models: Record<string, CatalogModel>
}

export type Catalog = Record<string, CatalogProvider>

let memoryCache: Catalog | null = null
let memoryCacheAt = 0

function isFresh(): boolean {
  return memoryCache !== null && Date.now() - memoryCacheAt < TTL_MS
}

async function readCacheFile(): Promise<Catalog | null> {
  try {
    const raw = await readFile(cachePath(), "utf8")
    const parsed = JSON.parse(raw) as Catalog
    if (parsed && typeof parsed === "object") return parsed
  } catch {
    // no cache file or corrupt — fall through
  }
  return null
}

async function writeCacheFile(cat: Catalog): Promise<void> {
  try {
    const path = cachePath()
    await mkdir(dirname(path), { recursive: true })
    await writeFile(path, JSON.stringify(cat), "utf8")
  } catch {
    // cache write is best-effort
  }
}

export function catalogPath(): string {
  return cachePath()
}

export async function fetchCatalog(force = false): Promise<Catalog | null> {
  if (!force && isFresh()) return memoryCache

  const path = cachePath()
  if (!force) {
    try {
      const st = await stat(path)
      if (Date.now() - st.mtimeMs < TTL_MS) {
        const fileCache = await readCacheFile()
        if (fileCache) {
          memoryCache = fileCache
          memoryCacheAt = Date.now()
          return memoryCache
        }
      }
    } catch {
      // no cache file yet
    }
  }

  try {
    const res = await fetch(`${sourceUrl()}/api.json`, {
      headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`models.dev returned HTTP ${res.status}`)
    const data = (await res.json()) as Catalog
    if (!data || typeof data !== "object") throw new Error("models.dev payload not an object")
    memoryCache = data
    memoryCacheAt = Date.now()
    await writeCacheFile(data)
    return data
  } catch {
    // network failed — use a stale cache file if one exists
    const stale = await readCacheFile()
    if (stale) {
      memoryCache = stale
      memoryCacheAt = Date.now()
    }
    return stale
  }
}

export async function getCatalog(): Promise<Catalog | null> {
  if (isFresh()) return memoryCache
  return fetchCatalog(false)
}

export function cachedCatalog(): Catalog | null {
  return isFresh() ? memoryCache : null
}

export function _resetForTest(): void {
  memoryCache = null
  memoryCacheAt = 0
}
