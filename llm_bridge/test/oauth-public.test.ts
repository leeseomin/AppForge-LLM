import { expect, test } from "bun:test"
import {
  publicOAuthPollResult,
  publicOAuthRefreshResult,
} from "../src/oauth/public"
import { listOAuthProviders } from "../src/oauth"

const credential = {
  type: "oauth" as const,
  refresh: "refresh-secret-value",
  access: "access-secret-value",
  expires: 1_900_000_000_000,
  accountId: "acct-123",
}

test("OpenAI is the only provider exposed through OAuth", () => {
  expect(listOAuthProviders().map((provider) => provider.id)).toEqual(["openai"])
})

test("public OAuth poll result contains metadata but no credential", () => {
  const result = publicOAuthPollResult(
    { status: "success", provider: "openai", credential },
    "openai",
  )
  const raw = JSON.stringify(result)

  expect(result).toEqual({
    status: "success",
    provider: "openai",
    accountId: "acct-123",
    expires: credential.expires,
  })
  expect(raw).not.toContain(credential.access)
  expect(raw).not.toContain(credential.refresh)
  expect(raw).not.toContain("credential")
})

test("public OAuth refresh result contains metadata but no credential", () => {
  const result = publicOAuthRefreshResult("openai", credential)
  const raw = JSON.stringify(result)

  expect(result).toEqual({
    ok: true,
    provider: "openai",
    accountId: "acct-123",
    expires: credential.expires,
  })
  expect(raw).not.toContain(credential.access)
  expect(raw).not.toContain(credential.refresh)
  expect(raw).not.toContain("credential")
})
