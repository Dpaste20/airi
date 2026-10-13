export const BASE_URL = process.env.AIRI_URL ?? "http://localhost:8000"

export interface Health {
  status: string
  agent: string
}

export async function getHealth(signal?: AbortSignal): Promise<Health | null> {
  try {
    const res = await fetch(`${BASE_URL}/health`, { signal })
    if (!res.ok) return null
    return (await res.json()) as Health
  } catch {
    return null
  }
}

/** POST /chat/stream and yield text deltas from the SSE stream. */
export async function* streamChat(
  message: string,
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const res = await fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    let idx: number
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue
        const data = line.slice(5).trim()
        if (data === "[DONE]") return
        const payload = JSON.parse(data) as { delta?: string; error?: string }
        if (payload.error) throw new Error(payload.error)
        if (payload.delta) yield payload.delta
      }
    }
  }
}
