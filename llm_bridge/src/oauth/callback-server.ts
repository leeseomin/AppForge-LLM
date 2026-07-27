import { createServer, type Server } from "node:http"
import type { PkceCodes } from "./types"

interface PendingCallback {
  pkce: PkceCodes
  state: string
  resolve: (code: string) => void
  reject: (error: Error) => void
}

const HTML_SUCCESS = `<!doctype html>
<html>
  <head>
    <title>AppForge - Authorization Successful</title>
    <style>
      body { font-family: system-ui, -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #131010; color: #f1ecec; }
      .container { text-align: center; padding: 2rem; }
      h1 { color: #f1ecec; margin-bottom: 1rem; }
      p { color: #b7b1b1; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Authorization Successful</h1>
      <p>You can close this window and return to AppForge.</p>
    </div>
    <script>setTimeout(() => window.close(), 2000)</script>
  </body>
</html>`

function htmlError(message: string): string {
  const safe = message.replace(/[&<>"']/g, "")
  return `<!doctype html>
<html>
  <head><title>AppForge - Authorization Failed</title></head>
  <body style="font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #131010; color: #f1ecec;">
    <div style="text-align: center; padding: 2rem;">
      <h1 style="color: #fc533a;">Authorization Failed</h1>
      <p style="color: #b7b1b1;">${safe}</p>
    </div>
  </body>
</html>`
}

export class CallbackServer {
  private server: Server | null = null
  private pending: PendingCallback | null = null

  async start(port: number, host = "localhost"): Promise<string> {
    if (this.server) {
      return `http://${host}:${port}/callback`
    }
    return new Promise((resolve, reject) => {
      const server = createServer((req, res) => {
        const url = new URL(req.url || "/", `http://${host}:${port}`)
        if (url.pathname === "/callback" || url.pathname === "/auth/callback") {
          const code = url.searchParams.get("code")
          const state = url.searchParams.get("state")
          const error = url.searchParams.get("error_description") ?? url.searchParams.get("error")
          if (error) {
            this.pending?.reject(new Error(error))
            this.pending = null
            res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
            res.end(htmlError(error))
            return
          }
          if (!code) {
            this.pending?.reject(new Error("Missing authorization code"))
            this.pending = null
            res.writeHead(400, { "Content-Type": "text/html; charset=utf-8" })
            res.end(htmlError("Missing authorization code"))
            return
          }
          if (!this.pending || state !== this.pending.state) {
            this.pending?.reject(new Error("Invalid state"))
            this.pending = null
            res.writeHead(400, { "Content-Type": "text/html; charset=utf-8" })
            res.end(htmlError("Invalid state"))
            return
          }
          const current = this.pending
          this.pending = null
          current.resolve(code)
          res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
          res.end(HTML_SUCCESS)
          return
        }
        if (url.pathname === "/cancel") {
          this.pending?.reject(new Error("Login cancelled"))
          this.pending = null
          res.writeHead(200)
          res.end("Login cancelled")
          return
        }
        res.writeHead(404)
        res.end("Not found")
      })
      const onError = (err: Error) => {
        this.server = null
        reject(err)
      }
      server.once("error", onError)
      server.listen(port, host, () => {
        server.removeListener("error", onError)
        this.server = server
        resolve(`http://${host}:${port}/callback`)
      })
    })
  }

  waitForCallback(pkce: PkceCodes, state: string, timeoutMs = 5 * 60 * 1000): Promise<string> {
    if (this.pending) {
      this.pending.reject(new Error("Superseded by a newer authorize request"))
      this.pending = null
    }
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (this.pending) {
          this.pending = null
          reject(new Error("OAuth callback timeout - authorization took too long"))
        }
      }, timeoutMs)
      this.pending = {
        pkce,
        state,
        resolve: (code) => {
          clearTimeout(timeout)
          resolve(code)
        },
        reject: (error) => {
          clearTimeout(timeout)
          reject(error)
        },
      }
    })
  }

  stop(): void {
    if (this.server) {
      this.server.close()
      this.server = null
    }
    this.pending = null
  }
}
