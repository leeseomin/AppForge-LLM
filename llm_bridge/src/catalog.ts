import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { mkdir, readFile, stat, writeFile } from "node:fs/promises"
import { VERSION } from "./version"

function defaultConfigDir(): string {
  return resolve(
    process.env.APPFORGE_LLM_CONFIG_DIR || join(homedir(), ".appforge", "llm"),
  )
}
function cachePath(): string {
  return resolve(process.env.APPFORGE_MODELS_DEV_CACHE || join(defaultConfigDir(), "models-dev.json"))
}
function sourceUrl(): string {
  const raw = process.env.APPFORGE_MODELS_DEV_URL || "https://models.dev"
  const url = new URL(raw)
  const loopback = new Set(["127.0.0.1", "localhost", "::1"]).has(url.hostname)
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    throw new Error("The models catalog must use HTTPS unless it is loopback-only")
  }
  if (!url.pathname.endsWith("/api.json")) {
    url.pathname = `${url.pathname.replace(/\/+$/, "")}/api.json`
  }
  return url.toString()
}
const TTL_MS = 5 * 60 * 1000
const MAX_CATALOG_BYTES = 10 * 1024 * 1024
const USER_AGENT = `appforge-llm-bridge/${VERSION}`

export interface CatalogModel {
  id: string
  name: string
  tool_call?: boolean
  reasoning?: boolean
  attachment?: boolean
  cost?: {
    input?: number
    output?: number
    cache_read?: number
    cache_write?: number
  }
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
    const info = await stat(cachePath())
    if (!info.isFile() || info.size > MAX_CATALOG_BYTES) return null
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
    const res = await fetch(sourceUrl(), {
      headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`models.dev returned HTTP ${res.status}`)
    const declaredLength = Number(res.headers.get("content-length") || 0)
    if (declaredLength > MAX_CATALOG_BYTES) throw new Error("models.dev payload is too large")
    const raw = await res.text()
    if (new TextEncoder().encode(raw).length > MAX_CATALOG_BYTES) {
      throw new Error("models.dev payload is too large")
    }
    const data = JSON.parse(raw) as Catalog
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
