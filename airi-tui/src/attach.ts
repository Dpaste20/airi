import { toMarkdown } from "@firecrawl/anydoc"
import { existsSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { basename, extname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const MAX_BYTES = 25 * 1024 * 1024
const MAX_CHARS = 60_000
const PLAIN = new Set([
  ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".tsx", ".js", ".jsx",
  ".go", ".rs", ".c", ".h", ".cpp", ".java", ".sh", ".html", ".css", ".log", ".sql", ".xml", ".ini",
])

export interface Attachment {
  name: string
  path: string
  text: string
  truncated: boolean
}

/** Normalise a pasted/typed path: quotes, file://, escaped spaces, ~. */
export function resolvePath(raw: string, absoluteOnly: boolean): string | null {
  let p = raw.trim()
  if (!p || p.includes("\n")) return null
  if (/^(['"]).*\1$/.test(p)) p = p.slice(1, -1)
  if (p.startsWith("file://")) {
    try {
      p = fileURLToPath(p)
    } catch {
      return null
    }
  }
  p = p.replace(/\\(.)/g, "$1")
  if (p.startsWith("~/")) p = homedir() + p.slice(1)
  if (absoluteOnly && !/^(\/|[A-Za-z]:[\\/])/.test(p)) return null
  return resolve(p)
}

export const isFile = (p: string) => {
  try {
    return existsSync(p) && statSync(p).isFile()
  } catch {
    return false
  }
}

export async function loadAttachment(path: string): Promise<Attachment> {
  const size = statSync(path).size
  if (size > MAX_BYTES) throw new Error(`${basename(path)}: too large (${(size / 1048576).toFixed(1)} MB)`)

  let text: string
  try {
    text = PLAIN.has(extname(path).toLowerCase())
      ? await Bun.file(path).text()
      : await toMarkdown(path) // no `ocr` option: scanned PDFs fail locally, nothing leaves the machine
  } catch (e) {
    const code = (e as { code?: string }).code
    const msg = e instanceof Error ? e.message : String(e)
    throw new Error(`${basename(path)}: ${code === "NeedsOcr" ? "scanned PDF, needs OCR" : msg}`)
  }

  const truncated = text.length > MAX_CHARS
  return { name: basename(path), path, text: truncated ? text.slice(0, MAX_CHARS) : text, truncated }
}
