import { mkdir, mkdtemp } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import { join } from "node:path"

export async function makeBridgeTestDirectory(prefix: string): Promise<string> {
  // GitHub-hosted Windows runners may point TEMP outside the signed-in user's
  // profile. Production config integrity intentionally rejects that layout, so
  // native Windows tests use a disposable profile-local parent instead.
  const root = process.platform === "win32"
    ? join(homedir(), ".appforge-llm-test-tmp")
    : tmpdir()
  await mkdir(root, { recursive: true })
  return mkdtemp(join(root, prefix))
}
