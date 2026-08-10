import { lstat, mkdir, readFile, rm, stat, symlink, writeFile } from "node:fs/promises"
import { spawn } from "node:child_process"
import { dirname, isAbsolute, join } from "node:path"
import { tmpdir } from "node:os"
import { afterEach, expect, test } from "bun:test"
import { makeBridgeTestDirectory } from "./test-paths"
import {
  _resetForTest,
  _setDpapiCommandRunnerForTest,
  _setSecurityCommandRunnerForTest,
  _setSecretStoreForTest,
  _setWindowsAclCommandRunnerForTest,
  configPath,
  getActive,
  secretBackend,
  setProvider,
  getProvider,
  type SecretStore,
} from "../src/config"

type WindowsAclRequest = {
  path: string
  kind: "directory" | "file"
  mode: "parent" | "private"
  repair: boolean
}
type WindowsAclCommandRequest = WindowsAclRequest | { targets: WindowsAclRequest[] }

function windowsAclTargets(request: WindowsAclCommandRequest): WindowsAclRequest[] {
  return "targets" in request ? request.targets : [request]
}

const testDirectories = new Set<string>()

async function makeTestDir(prefix: string): Promise<string> {
  const directory = await makeBridgeTestDirectory(prefix)
  testDirectories.add(directory)
  return directory
}

function windowsPowerShellExecutable(): string {
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
  return join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
}

function runWindowsPowerShell(script: string, input: unknown): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      windowsPowerShellExecutable(),
      [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
      ],
      { stdio: ["pipe", "pipe", "pipe"], windowsHide: true },
    )
    const stdout: Buffer[] = []
    const stderr: Buffer[] = []
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk))
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk))
    child.once("error", reject)
    child.once("close", (code) => {
      if (code !== 0) {
        reject(new Error(Buffer.concat(stderr).toString("utf8") || `PowerShell exited with ${code}`))
        return
      }
      resolve(Buffer.concat(stdout).toString("utf8"))
    })
    child.stdin.end(JSON.stringify(input))
  })
}

const WINDOWS_ACL_SUMMARY_SCRIPT = `
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User
$allowed = @($current.Value, 'S-1-5-18', 'S-1-5-32-544')
$entries = @()
foreach ($path in @($request.paths)) {
  $acl = Get-Acl -LiteralPath ([IO.Path]::GetFullPath([string]$path))
  $hasCurrentFullControl = $false
  $hasInheritedRule = $false
  $hasUnexpectedRule = $false
  foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
    if ($rule.IsInherited) {
      $hasInheritedRule = $true
    }
    $sid = $rule.IdentityReference.Value
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $allowed -notcontains $sid) {
      $hasUnexpectedRule = $true
    }
    $rights = [int64]$rule.FileSystemRights
    if ($sid -eq $current.Value -and (($rights -band [int64][Security.AccessControl.FileSystemRights]::FullControl) -eq [int64][Security.AccessControl.FileSystemRights]::FullControl)) {
      $hasCurrentFullControl = $true
    }
  }
  $entries += [pscustomobject]@{
    path = [string]$path
    ownerIsCurrent = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $current.Value
    protected = [bool]$acl.AreAccessRulesProtected
    hasCurrentFullControl = $hasCurrentFullControl
    hasInheritedRule = $hasInheritedRule
    hasUnexpectedRule = $hasUnexpectedRule
  }
}
[Console]::Out.Write((@{ entries = @($entries) } | ConvertTo-Json -Compress -Depth 4))
`

const WINDOWS_GRANT_EVERYONE_MODIFY_SCRIPT = `
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
$path = [IO.Path]::GetFullPath([string]$request.path)
$item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
$acl = Get-Acl -LiteralPath $path
$everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
$inheritance = [Security.AccessControl.InheritanceFlags]::None
if ([bool]$item.PSIsContainer) {
  $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
}
$rule = [Security.AccessControl.FileSystemAccessRule]::new(
  $everyone,
  [Security.AccessControl.FileSystemRights]::Modify,
  $inheritance,
  [Security.AccessControl.PropagationFlags]::None,
  [Security.AccessControl.AccessControlType]::Allow
)
[void]$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $path -AclObject $acl
`

afterEach(async () => {
  delete process.env.APPFORGE_LLM_CONFIG
  delete process.env.APPFORGE_LLM_CONFIG_DIR
  delete process.env.APPFORGE_DATA_DIR
  delete process.env.APPFORGE_LLM_SECRET_BACKEND
  _resetForTest()
  await Promise.all(
    Array.from(testDirectories, (directory) => rm(directory, { recursive: true, force: true })),
  )
  testDirectories.clear()
})

test("macOS Keychain and Windows DPAPI are the secure platform defaults", () => {
  delete process.env.APPFORGE_LLM_SECRET_BACKEND

  const expected = process.platform === "darwin" ? "keychain" : process.platform === "win32" ? "dpapi" : "file"
  expect(secretBackend()).toBe(expected)
})

test("secret config paths are absolute and independent of web job data", () => {
  process.env.APPFORGE_LLM_CONFIG_DIR = "relative-llm-config"
  expect(isAbsolute(configPath())).toBeTrue()

  delete process.env.APPFORGE_LLM_CONFIG_DIR
  process.env.APPFORGE_DATA_DIR = join(tmpdir(), "web-job-state-must-not-own-secrets")
  expect(configPath().startsWith(process.env.APPFORGE_DATA_DIR)).toBeFalse()
})

test("file backend creates a private directory and atomic 0600 config file", async () => {
  const dir = await makeTestDir("appforge-bridge-file-perms-")
  const configDir = join(dir, "private")
  process.env.APPFORGE_LLM_CONFIG_DIR = configDir
  process.env.APPFORGE_LLM_CONFIG = join(configDir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-file-secret" })

  if (process.platform !== "win32") {
    expect((await stat(configDir)).mode & 0o077).toBe(0)
    expect((await stat(configPath())).mode & 0o077).toBe(0)
  }
})

test("file backend refuses to follow a symlinked config file", async () => {
  const dir = await makeTestDir("appforge-bridge-symlink-")
  const target = join(dir, "target.json")
  const link = join(dir, "providers.json")
  await writeFile(target, "do-not-overwrite", "utf8")
  try {
    await symlink(target, link, "file")
  } catch (error) {
    const code = (error as { code?: unknown }).code
    if (process.platform === "win32" && (code === "EPERM" || code === "EACCES")) return
    throw error
  }
  process.env.APPFORGE_LLM_CONFIG = link
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await expect(setProvider("openai", { apiKey: "sk-file-secret" })).rejects.toThrow()
  expect(await readFile(target, "utf8")).toBe("do-not-overwrite")
  expect((await lstat(link)).isSymbolicLink()).toBe(true)
})

test("Windows ACL gate protects the config directory and every atomic file write", async () => {
  const dir = await makeTestDir("appforge-bridge-acl-contract-")
  const configDir = join(dir, "private")
  const providerPath = join(configDir, "providers.json")
  const secret = "sk-not-part-of-acl-command"
  const calls: Array<{ args: string[]; input?: string; request: WindowsAclCommandRequest }> = []
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()
  _setWindowsAclCommandRunnerForTest(async (args, input) => {
    const request = JSON.parse(input || "{}") as WindowsAclCommandRequest
    calls.push({ args: [...args], input, request })
    return { stdout: "ok" }
  })

  await setProvider("openai-compatible", {
    apiKey: secret,
    baseURL: "https://trusted.example/v1",
  })

  const requests = calls.flatMap(({ request }) => windowsAclTargets(request))
  expect(requests.some((request) =>
    request.path === dir
    && request.kind === "directory"
    && request.mode === "parent"
    && request.repair === false,
  )).toBeTrue()
  expect(requests.some((request) =>
    request.path === configDir
    && request.kind === "directory"
    && request.mode === "private"
    && request.repair === true,
  )).toBeTrue()
  expect(requests.some((request) =>
    dirname(request.path) === configDir
    && request.kind === "file"
    && request.mode === "private"
    && request.repair === true
    && request.path.includes(".providers.json.")
    && request.path.endsWith(".tmp"),
  )).toBeTrue()
  expect(requests.some((request) =>
    request.path === providerPath
    && request.kind === "file"
    && request.mode === "private"
    && request.repair === false,
  )).toBeTrue()
  expect(calls.every(({ args, input }) =>
    !args.join(" ").includes(secret) && !(input || "").includes(secret),
  )).toBeTrue()
})

test("Windows ACL gate batches cold directory initialization into one PowerShell call", async () => {
  const dir = await makeTestDir("appforge-bridge-acl-cold-start-")
  const configDir = join(dir, "private")
  const providerPath = join(configDir, "providers.json")
  const calls: unknown[] = []
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()
  _setWindowsAclCommandRunnerForTest(async (_args, input) => {
    calls.push(JSON.parse(input || "{}"))
    return { stdout: "ok" }
  })

  expect(await getActive()).toEqual({ provider: null, model: null })

  expect(calls).toHaveLength(1)
  const request = calls[0] as { targets: WindowsAclRequest[] }
  expect(request.targets).toContainEqual({
    path: dir,
    kind: "directory",
    mode: "parent",
    repair: false,
  })
  expect(request.targets.at(-1)).toEqual({
    path: configDir,
    kind: "directory",
    mode: "private",
    repair: true,
  })
})

test("Windows ACL gate fails closed before writing into an unsafe parent", async () => {
  const dir = await makeTestDir("appforge-bridge-acl-parent-")
  const providerPath = join(dir, "private", "providers.json")
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()
  _setWindowsAclCommandRunnerForTest(async (_args, input) => {
    const request = JSON.parse(input || "{}") as WindowsAclCommandRequest
    const failedIndex = windowsAclTargets(request).findIndex((target) => target.mode === "parent")
    return { stdout: failedIndex >= 0 ? `error:${failedIndex}` : "ok" }
  })

  await expect(setProvider("openai-compatible", {
    apiKey: "sk-must-not-be-written",
    baseURL: "https://trusted.example/v1",
  })).rejects.toThrow("Windows ACL validation failed for config parent directory")
  await expect(readFile(providerPath, "utf8")).rejects.toThrow()
})

test("Windows ACL gate refuses to trust a provider file that may have been tampered with", async () => {
  const dir = await makeTestDir("appforge-bridge-acl-tampered-")
  const providerPath = join(dir, "providers.json")
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  await writeFile(providerPath, JSON.stringify({
    providers: {
      "openai-compatible": {
        apiKeyRef: "dpapi:appforge-llm/openai-compatible/apiKey",
        baseURL: "https://attacker.invalid/v1",
      },
    },
    active: { provider: "openai-compatible", model: null },
  }), "utf8")
  _resetForTest()
  let secretReads = 0
  _setSecretStoreForTest({
    async get() {
      secretReads += 1
      return "sk-must-never-be-decrypted"
    },
    async set() {},
    async delete() {},
  })
  _setWindowsAclCommandRunnerForTest(async (_args, input) => {
    const request = JSON.parse(input || "{}") as WindowsAclRequest
    if (request.path === providerPath && request.kind === "file") {
      throw new Error("writable by another identity")
    }
    return { stdout: "ok" }
  })

  await expect(getProvider("openai-compatible")).rejects.toThrow(
    "Windows ACL validation failed for config file",
  )
  expect(secretReads).toBe(0)
})

test("keychain backend stores provider secrets as JSON references", async () => {
  const dir = await makeTestDir("appforge-bridge-config-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  const secrets = new Map<string, string>()
  const store: SecretStore = {
    async get(providerId, key) {
      return secrets.get(`${providerId}:${key}`)
    },
    async set(providerId, key, value) {
      secrets.set(`${providerId}:${key}`, value)
    },
    async delete(providerId, key) {
      secrets.delete(`${providerId}:${key}`)
    },
  }
  _resetForTest()
  _setSecretStoreForTest(store)

  await setProvider("openai", {
    apiKey: "sk-test-secret",
    baseURL: "https://example.test/v1",
    defaultModel: "gpt-4o-mini",
  })

  const raw = await readFile(configPath(), "utf8")
  const payload = JSON.parse(raw)
  expect(raw).not.toContain("sk-test-secret")
  expect(payload.providers.openai.apiKey).toBeUndefined()
  expect(payload.providers.openai.apiKeyRef).toBe("keychain:appforge-llm/openai/apiKey")
  expect(secrets.get("openai:apiKey")).toBe("sk-test-secret")
  expect((await getProvider("openai"))?.apiKey).toBe("sk-test-secret")
})

test("dpapi backend stores only a reference in provider JSON", async () => {
  const dir = await makeTestDir("appforge-bridge-dpapi-config-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  const secrets = new Map<string, string>()
  const store: SecretStore = {
    async get(providerId, key) {
      return secrets.get(`${providerId}:${key}`)
    },
    async set(providerId, key, value) {
      secrets.set(`${providerId}:${key}`, value)
    },
    async delete(providerId, key) {
      secrets.delete(`${providerId}:${key}`)
    },
  }
  _resetForTest()
  _setSecretStoreForTest(store)

  await setProvider("deepseek", { apiKey: "sk-dpapi-secret", defaultModel: "deepseek-chat" })

  const raw = await readFile(configPath(), "utf8")
  const payload = JSON.parse(raw)
  expect(raw).not.toContain("sk-dpapi-secret")
  expect(payload.providers.deepseek.apiKey).toBeUndefined()
  expect(payload.providers.deepseek.apiKeyRef).toBe("dpapi:appforge-llm/deepseek/apiKey")
  expect((await getProvider("deepseek"))?.apiKey).toBe("sk-dpapi-secret")
})

test("dpapi command receives secret material only through stdin", async () => {
  const dir = await makeTestDir("appforge-bridge-dpapi-stdin-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  const secret = "sk-never-in-process-arguments"
  const secretBase64 = Buffer.from(secret, "utf8").toString("base64")
  const encrypted = Buffer.from("opaque-ciphertext", "utf8").toString("base64")
  const calls: Array<{ args: string[]; input?: string }> = []
  _resetForTest()
  _setDpapiCommandRunnerForTest(async (args, input) => {
    calls.push({ args: [...args], input })
    const request = JSON.parse(input || "{}")
    return { stdout: request.operation === "protect" ? encrypted : secretBase64 }
  })

  await setProvider("openrouter", { apiKey: secret })

  expect(calls.length).toBeGreaterThanOrEqual(2)
  for (const call of calls) {
    expect(call.args.join(" ")).not.toContain(secret)
    expect(call.args.join(" ")).not.toContain(secretBase64)
  }
  expect(calls.some((call) => call.input?.includes(secretBase64))).toBe(true)
})

test("plaintext Windows config is migrated into the secure backend on load", async () => {
  const dir = await makeTestDir("appforge-bridge-dpapi-migrate-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  await writeFile(
    configPath(),
    JSON.stringify({
      providers: { openai: { apiKey: "legacy-plaintext", defaultModel: "gpt-4.1-mini" } },
      active: { provider: "openai", model: "gpt-4.1-mini" },
    }),
    "utf8",
  )
  const secrets = new Map<string, string>()
  _resetForTest()
  _setSecretStoreForTest({
    async get(providerId, key) { return secrets.get(`${providerId}:${key}`) },
    async set(providerId, key, value) { secrets.set(`${providerId}:${key}`, value) },
    async delete(providerId, key) { secrets.delete(`${providerId}:${key}`) },
  })

  expect((await getProvider("openai"))?.apiKey).toBe("legacy-plaintext")
  const raw = await readFile(configPath(), "utf8")
  expect(raw).not.toContain("legacy-plaintext")
  expect(JSON.parse(raw).providers.openai.apiKeyRef).toBe("dpapi:appforge-llm/openai/apiKey")
})

test("keychain command writes through interactive stdin without exposing the secret in argv", async () => {
  if (process.platform !== "darwin") return
  const dir = await makeTestDir("appforge-bridge-keychain-stdin-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  const secret = ["sk", "never", "in", "process", "arguments"].join("-")
  const calls: Array<{ args: string[]; input?: string }> = []
  _resetForTest()
  _setSecurityCommandRunnerForTest(async (args, input) => {
    calls.push({ args: [...args], input })
    if (args[0] === "find-generic-password") return { stdout: `${secret}\n` }
    return { stdout: "" }
  })

  await setProvider("openai", { apiKey: secret })

  const add = calls.find((call) => call.args.includes("-i"))
  expect(add).toBeDefined()
  expect(add?.args).not.toContain(secret)
  expect(add?.args).toEqual(["-q", "-i"])
  expect(add?.input).toBe(
    `add-generic-password -U -s appforge-llm -a openai:apiKey -X ${Buffer.from(secret, "utf8").toString("hex")}\n`,
  )
  expect(add?.input).not.toContain(secret)
})

test("keychain backend rejects a write that cannot be read back", async () => {
  if (process.platform !== "darwin") return
  const dir = await makeTestDir("appforge-bridge-keychain-readback-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "keychain"
  _resetForTest()
  _setSecurityCommandRunnerForTest(async (args) => {
    if (args[0] === "find-generic-password") return { stdout: "" }
    return { stdout: "" }
  })

  await expect(setProvider("deepseek", { apiKey: "sk-readback-must-match" })).rejects.toThrow(
    "could not be verified",
  )
  expect((await getProvider("deepseek"))?.apiKey).toBeUndefined()
  await expect(readFile(configPath(), "utf8")).rejects.toThrow()
})

test("omitted or null keys preserve the credential and explicit clearing removes it", async () => {
  const dir = await makeTestDir("appforge-bridge-key-semantics-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-existing" })
  await setProvider("openai", { defaultModel: "gpt-4o-mini" })
  expect((await getProvider("openai"))?.apiKey).toBe("sk-existing")
  await setProvider("openai", { apiKey: null })
  expect((await getProvider("openai"))?.apiKey).toBe("sk-existing")
  await setProvider("openai", { clearApiKey: true })
  expect((await getProvider("openai"))?.apiKey).toBeUndefined()
})

test("legacy OAuth fields are discarded while API-key settings are preserved", async () => {
  const dir = await makeTestDir("appforge-bridge-legacy-oauth-")
  process.env.APPFORGE_LLM_CONFIG = join(dir, "providers.json")
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  await writeFile(
    configPath(),
    JSON.stringify({
      providers: {
        openai: {
          apiKey: "sk-existing",
          defaultModel: "gpt-4o-mini",
          oauth: {
            type: "oauth",
            access: "legacy-access-token",
            refresh: "legacy-refresh-token",
            expires: 1_900_000_000_000,
          },
          oauthRef: "keychain:appforge-llm/openai/oauth",
        },
      },
      active: { provider: "openai", model: "gpt-4o-mini" },
    }),
    "utf8",
  )
  _resetForTest()

  const provider = await getProvider("openai")
  expect((provider as unknown as Record<string, unknown>).oauth).toBeUndefined()
  expect((provider as unknown as Record<string, unknown>).oauthRef).toBeUndefined()
  expect(provider?.apiKey).toBe("sk-existing")

  await setProvider("openai", { defaultModel: "gpt-4.1-mini" })
  const raw = await readFile(configPath(), "utf8")
  expect(raw.toLowerCase()).not.toContain("oauth")
  expect(JSON.parse(raw).providers.openai.apiKey).toBe("sk-existing")
})

test("Windows DPAPI backend performs a real CurrentUser round trip with private DACLs", async () => {
  if (process.platform !== "win32") return
  const dir = await makeTestDir("appforge-bridge-dpapi-real-")
  const providerPath = join(dir, "providers.json")
  const dpapiPath = join(dir, "secrets.dpapi.json")
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  _resetForTest()

  await setProvider("openai", { apiKey: "sk-windows-dpapi-roundtrip" })

  expect((await getProvider("openai"))?.apiKey).toBe("sk-windows-dpapi-roundtrip")
  expect(await readFile(providerPath, "utf8")).not.toContain("sk-windows-dpapi-roundtrip")
  expect(await readFile(dpapiPath, "utf8")).not.toContain("sk-windows-dpapi-roundtrip")

  const summary = JSON.parse(await runWindowsPowerShell(WINDOWS_ACL_SUMMARY_SCRIPT, {
    paths: [dir, providerPath, dpapiPath],
  })) as {
    entries: Array<{
      ownerIsCurrent: boolean
      protected: boolean
      hasCurrentFullControl: boolean
      hasInheritedRule: boolean
      hasUnexpectedRule: boolean
    }>
  }
  expect(summary.entries).toHaveLength(3)
  for (const entry of summary.entries) {
    expect(entry.ownerIsCurrent).toBeTrue()
    expect(entry.protected).toBeTrue()
    expect(entry.hasCurrentFullControl).toBeTrue()
    expect(entry.hasInheritedRule).toBeFalse()
    expect(entry.hasUnexpectedRule).toBeFalse()
  }
})

test("Windows ACL bridge supports non-ASCII user and config paths", async () => {
  if (process.platform !== "win32") return
  const dir = await makeTestDir("appforge-bridge-acl-unicode-")
  const configDir = join(dir, "한글 설정")
  const providerPath = join(configDir, "providers.json")
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
  _resetForTest()

  await setProvider("openai-compatible", {
    apiKey: "sk-unicode-path",
    baseURL: "https://trusted.example/v1",
  })

  const payload = JSON.parse(await readFile(providerPath, "utf8"))
  expect(payload.providers["openai-compatible"].baseURL).toBe("https://trusted.example/v1")
})

test("Windows ACL gate rejects a real parent DACL writable by Everyone", async () => {
  if (process.platform !== "win32") return
  const unsafeParent = await makeTestDir("appforge-bridge-acl-real-unsafe-")
  const providerPath = join(unsafeParent, "private", "providers.json")
  try {
    await runWindowsPowerShell(WINDOWS_GRANT_EVERYONE_MODIFY_SCRIPT, { path: unsafeParent })
    process.env.APPFORGE_LLM_CONFIG = providerPath
    process.env.APPFORGE_LLM_SECRET_BACKEND = "file"
    _resetForTest()

    await expect(setProvider("openai-compatible", {
      apiKey: "sk-must-stay-local",
      baseURL: "https://trusted.example/v1",
    })).rejects.toThrow("Windows ACL validation failed for config parent directory")
    await expect(readFile(providerPath, "utf8")).rejects.toThrow()
  } finally {
    await rm(unsafeParent, { recursive: true, force: true })
  }
})

test("Windows ACL gate rejects a real provider file writable by Everyone before secret hydration", async () => {
  if (process.platform !== "win32") return
  const dir = await makeTestDir("appforge-bridge-acl-real-file-")
  const configDir = join(dir, "private")
  const providerPath = join(configDir, "providers.json")
  await mkdir(configDir, { recursive: true })
  await writeFile(providerPath, JSON.stringify({
    providers: {
      "openai-compatible": {
        apiKeyRef: "dpapi:appforge-llm/openai-compatible/apiKey",
        baseURL: "https://attacker.invalid/v1",
      },
    },
    active: { provider: "openai-compatible", model: null },
  }), "utf8")
  await runWindowsPowerShell(WINDOWS_GRANT_EVERYONE_MODIFY_SCRIPT, { path: providerPath })
  process.env.APPFORGE_LLM_CONFIG = providerPath
  process.env.APPFORGE_LLM_SECRET_BACKEND = "dpapi"
  _resetForTest()
  let secretReads = 0
  _setSecretStoreForTest({
    async get() {
      secretReads += 1
      return "sk-must-never-be-decrypted"
    },
    async set() {},
    async delete() {},
  })

  await expect(getProvider("openai-compatible")).rejects.toThrow(
    "Windows ACL validation failed for config file",
  )
  expect(secretReads).toBe(0)
})
