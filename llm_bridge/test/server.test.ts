import { mkdtemp } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { expect, test } from "bun:test"
import { _resetForTest as resetRegistry } from "../src/registry"
import { _resetForTest as resetCatalog } from "../src/catalog"
import * as oauth from "../src/oauth"
import * as store from "../src/config"

const BRIDGE_TOKEN = "test-bridge-capability-0123456789abcdef0123456789"

function authorizedRequest(url: string, init: RequestInit = {}): Request {
  const headers = new Headers(init.headers)
  headers.set("x-appforge-bridge-token", BRIDGE_TOKEN)
  return new Request(url, { ...init, headers })
}

async function createIsolatedApp(prefix: string) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_CONFIG_DIR = dir
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  process.env.APPFORGE_LLM_BRIDGE_TOKEN = BRIDGE_TOKEN
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
    authorizedRequest("http://127.0.0.1/providers/openai", {
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

  const compactResponse = await app.fetch(authorizedRequest("http://127.0.0.1/providers"))
  expect(compactResponse.status).toBe(200)
  const compactPayload = await compactResponse.json()
  const compactOpenAI = compactPayload.providers.find((provider: { id: string }) => provider.id === "openai")
  expect(compactOpenAI.models).toEqual([])
  expect(compactOpenAI.model_count).toBeGreaterThan(0)

  const fullResponse = await app.fetch(authorizedRequest("http://127.0.0.1/providers?include_models=true"))
  expect(fullResponse.status).toBe(200)
  const fullPayload = await fullResponse.json()
  const fullOpenAI = fullPayload.providers.find((provider: { id: string }) => provider.id === "openai")
  expect(fullOpenAI.models.map((model: { id: string }) => model.id)).toContain("gpt-4o-mini")
})

test("all-provider model catalog endpoint is not exposed", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-no-all-models-")

  const response = await app.fetch(authorizedRequest("http://127.0.0.1/providers/models"))

  expect(response.status).toBe(404)
})

test("OAuth poll and refresh responses never expose stored credentials", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-oauth-")
  const handler = oauth.getOAuthProvider("openai")
  if (!handler) throw new Error("openai OAuth handler missing")
  const originalPoll = handler.poll
  const originalRefresh = handler.refresh
  const initial = {
    type: "oauth" as const,
    refresh: "initial-refresh-secret",
    access: "initial-access-secret",
    expires: 1_900_000_000_000,
    accountId: "acct-initial",
  }
  const refreshed = {
    ...initial,
    refresh: "refreshed-refresh-secret",
    access: "refreshed-access-secret",
    expires: initial.expires + 60_000,
    accountId: "acct-refreshed",
  }

  try {
    handler.poll = () => ({ status: "success", provider: "openai", credential: initial })
    handler.refresh = async () => refreshed

    const pollResponse = await app.fetch(
      authorizedRequest("http://127.0.0.1/oauth/poll/openai/poll-1"),
    )
    expect(pollResponse.status).toBe(200)
    const pollRaw = await pollResponse.text()
    expect(pollRaw).not.toContain(initial.access)
    expect(pollRaw).not.toContain(initial.refresh)
    expect(pollRaw).not.toContain("credential")
    expect(JSON.parse(pollRaw)).toEqual({
      status: "success",
      provider: "openai",
      accountId: "acct-initial",
      expires: initial.expires,
    })
    expect((await store.getOAuthCredential("openai"))?.access).toBe(initial.access)

    const refreshResponse = await app.fetch(
      authorizedRequest("http://127.0.0.1/oauth/refresh/openai", { method: "POST" }),
    )
    expect(refreshResponse.status).toBe(200)
    const refreshRaw = await refreshResponse.text()
    expect(refreshRaw).not.toContain(refreshed.access)
    expect(refreshRaw).not.toContain(refreshed.refresh)
    expect(refreshRaw).not.toContain("credential")
    expect(JSON.parse(refreshRaw)).toEqual({
      ok: true,
      provider: "openai",
      accountId: "acct-refreshed",
      expires: refreshed.expires,
    })
    expect((await store.getOAuthCredential("openai"))?.access).toBe(refreshed.access)
  } finally {
    handler.poll = originalPoll
    handler.refresh = originalRefresh
  }
})

test("agent accepts a tool result that arrives before model streaming finishes", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-agent-race-")
  const { _setAgentStreamForTest } = await import("../src/server")
  let streamPass = 0
  _setAgentStreamForTest(async (_request, onEvent) => {
    streamPass += 1
    if (streamPass === 1) {
      onEvent({
        type: "tool-call",
        call_id: "call-fast",
        id: "call-fast",
        name: "write_text",
        arguments: { path: "index.html", content: "ok" },
      })
      // Keep the model stream open so the HTTP tool result reaches the bridge
      // before runAgentSession starts waiting for it.
      await Bun.sleep(50)
      onEvent({ type: "finish", reason: "tool-calls", usage: { total_tokens: 3 } })
    } else {
      onEvent({ type: "finish", reason: "stop", usage: { total_tokens: 1 } })
    }
    return { provider: "test", model: "test-model" }
  })

  try {
    const started = await app.fetch(
      authorizedRequest("http://127.0.0.1/agent/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          prompt: "write a file",
          tools: [{ name: "write_text", parameters: { type: "object" } }],
        }),
      }),
    )
    expect(started.status).toBe(200)
    const { session_id: sessionId } = await started.json()

    const eventsResponse = await app.fetch(
      authorizedRequest(`http://127.0.0.1/agent/${sessionId}/events`),
    )
    expect(eventsResponse.status).toBe(200)
    const reader = eventsResponse.body!.getReader()
    const decoder = new TextDecoder()
    let events = ""
    while (!events.includes("event: tool_call")) {
      const chunk = await reader.read()
      expect(chunk.done).toBe(false)
      events += decoder.decode(chunk.value, { stream: true })
    }

    const earlyResult = await app.fetch(
      authorizedRequest(`http://127.0.0.1/agent/${sessionId}/tool_result`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          call_id: "call-fast",
          result: "written",
          is_error: false,
        }),
      }),
    )
    expect(earlyResult.status).toBe(200)
    expect((await earlyResult.json()).queued).toBe(true)

    while (!events.includes('"total_tokens":4')) {
      const chunk = await reader.read()
      if (chunk.done) break
      events += decoder.decode(chunk.value, { stream: true })
    }
    expect(events).toContain("event: tool_result")
    expect(events).toContain("event: done")
    expect(events).toContain('"total_tokens":4')
    expect(streamPass).toBe(2)

    const duplicate = await app.fetch(
      authorizedRequest(`http://127.0.0.1/agent/${sessionId}/tool_result`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          call_id: "call-fast",
          result: "written",
          is_error: false,
        }),
      }),
    )
    expect(duplicate.status).toBe(200)
    expect((await duplicate.json()).duplicate).toBe(true)
  } finally {
    _setAgentStreamForTest(null)
  }
})

test("health is minimal and all credential-bearing routes require the bridge token", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-auth-")

  const healthResponse = await app.fetch(new Request("http://127.0.0.1/health"))
  expect(healthResponse.status).toBe(200)
  expect(await healthResponse.json()).toEqual({
    ok: true,
    service: "appforge-llm-bridge",
    version: "0.7.0",
  })

  const denied = await app.fetch(new Request("http://127.0.0.1/providers"))
  expect(denied.status).toBe(401)
  expect((await denied.json()).error.code).toBe("BRIDGE_AUTH_REQUIRED")
})

test("bridge rejects cross-origin and non-JSON credential updates", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-request-guard-")

  const crossOrigin = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { origin: "https://attacker.example", "content-type": "application/json" },
    body: JSON.stringify({ apiKey: "sk-test-value" }),
  }))
  expect(crossOrigin.status).toBe(403)
  expect((await crossOrigin.json()).error.code).toBe("FORBIDDEN_ORIGIN")

  const wrongLoopbackPort = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { origin: "http://127.0.0.1:9999", "content-type": "application/json" },
    body: JSON.stringify({ apiKey: "sk-test-value" }),
  }))
  expect(wrongLoopbackPort.status).toBe(403)

  const wrongType = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { "content-type": "text/plain" },
    body: JSON.stringify({ apiKey: "sk-test-value" }),
  }))
  expect(wrongType.status).toBe(415)
  expect((await wrongType.json()).error.code).toBe("JSON_CONTENT_TYPE_REQUIRED")
})

test("provider updates preserve an omitted key and clear only through an explicit flag", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-update-")
  await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ apiKey: "sk-existing-secret", defaultModel: "gpt-4o-mini" }),
  }))

  const update = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ defaultModel: "gpt-4.1-mini" }),
  }))
  expect(update.status).toBe(200)
  expect((await store.getProvider("openai"))?.apiKey).toBe("sk-existing-secret")

  const cleared = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clearApiKey: true }),
  }))
  expect(cleared.status).toBe(200)
  expect((await store.getProvider("openai"))?.apiKey).toBeUndefined()
})

test("a caller-supplied custom endpoint cannot receive an existing stored key", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-endpoint-pairing-")
  await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai-compatible", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ apiKey: "stored-custom-key", baseURL: "https://trusted.example/v1", defaultModel: "model" }),
  }))

  const response = await app.fetch(authorizedRequest("http://127.0.0.1/providers/openai-compatible/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ baseURL: "https://attacker.example/v1", model: "model" }),
  }))

  expect(response.status).toBe(422)
  expect((await response.json()).error.code).toBe("EXPLICIT_KEY_REQUIRED_FOR_CUSTOM_ENDPOINT")
})

test("stream errors redact provider credentials before returning them", async () => {
  const app = await createIsolatedApp("appforge-llm-bridge-error-redaction-")
  const { _setAgentStreamForTest } = await import("../src/server")
  const { BridgeLLMError } = await import("../src/llm")
  const secret = `sk-proj-${"z".repeat(48)}`
  _setAgentStreamForTest(async () => {
    throw new BridgeLLMError(`upstream rejected ${secret}`)
  })
  try {
    const started = await app.fetch(authorizedRequest("http://127.0.0.1/agent/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt: "test redaction" }),
    }))
    const { session_id: sessionId } = await started.json()
    const events = await app.fetch(
      authorizedRequest(`http://127.0.0.1/agent/${sessionId}/events`),
    )
    const raw = await events.text()
    expect(raw).not.toContain(secret)
    expect(raw).toContain("[REDACTED]")
  } finally {
    _setAgentStreamForTest(null)
  }
})
