import { expect, test } from "bun:test"
import { get, statusOf } from "../src/registry"

test("deepseek defaults to v4 pro and keeps it selectable", () => {
  const entry = get("deepseek")
  expect(entry).toBeDefined()
  if (!entry) return

  const status = statusOf(entry, undefined)

  expect(status.default_model).toBe("deepseek-v4-pro")
  expect(status.models[0].id).toBe("deepseek-v4-pro")
  expect(status.models.map((model) => model.id)).toContain("deepseek-v4-pro")
})

test("deepseek legacy defaults are normalized to v4 pro", () => {
  const entry = get("deepseek")
  expect(entry).toBeDefined()
  if (!entry) return

  expect(statusOf(entry, { defaultModel: "deepseek-chat" }).default_model).toBe("deepseek-v4-pro")
  expect(statusOf(entry, { defaultModel: "deepseek-reasoner" }).default_model).toBe("deepseek-v4-pro")
})
