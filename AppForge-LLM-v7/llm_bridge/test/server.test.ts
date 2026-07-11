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
      new Request("http://127.0.0.1/agent/start", {
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
      new Request(`http://127.0.0.1/agent/${sessionId}/events`),
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
      new Request(`http://127.0.0.1/agent/${sessionId}/tool_result`, {
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

    while (!events.includes("event: done")) {
      const chunk = await reader.read()
      if (chunk.done) break
      events += decoder.decode(chunk.value, { stream: true })
    }
    expect(events).toContain("event: tool_result")
    expect(events).toContain("event: done")
    expect(streamPass).toBe(2)

    const duplicate = await app.fetch(
      new Request(`http://127.0.0.1/agent/${sessionId}/tool_result`, {
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
