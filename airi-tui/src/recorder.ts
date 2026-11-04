const RATE = 16000

function wav(pcm: Uint8Array): Uint8Array<ArrayBuffer> {
  const h = new DataView(new ArrayBuffer(44))
  const s = (o: number, t: string) => [...t].forEach((c, i) => h.setUint8(o + i, c.charCodeAt(0)))
  s(0, "RIFF"); h.setUint32(4, 36 + pcm.length, true); s(8, "WAVE")
  s(12, "fmt "); h.setUint32(16, 16, true); h.setUint16(20, 1, true); h.setUint16(22, 1, true)
  h.setUint32(24, RATE, true); h.setUint32(28, RATE * 2, true); h.setUint16(32, 2, true); h.setUint16(34, 16, true)
  s(36, "data"); h.setUint32(40, pcm.length, true)
  const out = new Uint8Array(44 + pcm.length)
  out.set(new Uint8Array(h.buffer), 0)
  out.set(pcm, 44)
  return out
}

export interface Recording {
  stop(): Promise<Uint8Array<ArrayBuffer>>
  cancel(): void
}

/** Linux/ALSA mic capture. Throws synchronously if `arecord` is missing. */
export function startRecording(): Recording {
  const proc = Bun.spawn(
    ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", String(RATE), "-c", "1"],
    { stdout: "pipe", stderr: "pipe" },
  )
  const chunks: Uint8Array[] = []
  const pump = (async () => {
    for await (const c of proc.stdout) chunks.push(c)
  })()
  const errText = new Response(proc.stderr).text()

  return {
    async stop() {
      proc.kill()
      await pump
      const n = chunks.reduce((a, c) => a + c.length, 0)
      if (n < RATE * 2 * 0.3) {
        const err = (await errText).trim()
        throw new Error(err ? `mic: ${err}` : "recording too short")
      }
      const pcm = new Uint8Array(n)
      let o = 0
      for (const c of chunks) {
        pcm.set(c, o)
        o += c.length
      }
      return wav(pcm)
    },
    cancel() {
      proc.kill()
    },
  }
}
