export const BASE_URL = process.env.AIRI_URL ?? "http://localhost:8000"

export interface Health {
  status: string
  agent: string
  model?: string
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

export interface StreamChunk {
  text?: string
  thinking?: string
}

/** POST /chat/stream and yield text / thinking deltas from the SSE stream. */
export async function* streamChat(
  message: string,
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<StreamChunk> {
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
        const payload = JSON.parse(data) as { delta?: string; thinking?: string; error?: string }
        if (payload.error) throw new Error(payload.error)
        if (payload.thinking) yield { thinking: payload.thinking }
        if (payload.delta) yield { text: payload.delta }
      }
    }
  }
}

/** POST a WAV to /transcribe (proxied to speech.cpp) and return the text. */
export async function transcribe(wav: Uint8Array<ArrayBuffer>, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`${BASE_URL}/transcribe`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: new Blob([wav], { type: "audio/wav" }),
    signal,
  })
  if (!res.ok) throw new Error(`ASR ${res.status}: ${(await res.text()).slice(0, 200)}`)
  return (((await res.json()) as { text?: string }).text ?? "").trim()
}

/** Barge-in: stop Airi's current speech. */
export const stopAudio = () => void fetch(`${BASE_URL}/audio/stop`, { method: "POST" }).catch(() => {})
