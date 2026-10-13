import { useCallback, useEffect, useRef, useState } from "react"
import type { ScrollBoxRenderable, SyntaxStyle } from "@opentui/core"
import { useKeyboard, useTerminalDimensions } from "@opentui/react"
import { BASE_URL, getHealth, streamChat } from "./api"
import { LOGO, LOGO_COLORS } from "./logo"
import { theme as t } from "./theme"

const VERSION = "2.0.0"
const MODEL = "gemma4:31b-cloud"
const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

interface Msg {
  id: number
  role: "user" | "assistant" | "error"
  text: string
}

const newSession = () => crypto.randomUUID()

function useSpinner(active: boolean) {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (!active) return
    const h = setInterval(() => setI((n) => (n + 1) % SPINNER.length), 80)
    return () => clearInterval(h)
  }, [active])
  return SPINNER[i]
}

export function App({ syntaxStyle }: { syntaxStyle: SyntaxStyle }) {
  const { width } = useTerminalDimensions()

  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [online, setOnline] = useState<boolean | null>(null)
  const [agent, setAgent] = useState("Airi")
  const [sessionId, setSessionId] = useState(newSession)

  const scrollRef = useRef<ScrollBoxRenderable | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const nextId = useRef(1)
  const spin = useSpinner(busy)

  // ---- health polling ----------------------------------------------------
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const h = await getHealth()
      if (!alive) return
      setOnline(!!h)
      if (h?.agent) setAgent(h.agent)
    }
    tick()
    const h = setInterval(tick, 5000)
    return () => {
      alive = false
      clearInterval(h)
    }
  }, [])

  // ---- actions -----------------------------------------------------------
  const reset = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setBusy(false)
    setSessionId(newSession())
  }, [])

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text || busy) return

      const userId = nextId.current++
      const botId = nextId.current++
      setInput("")
      setBusy(true)
      setMessages((m) => [
        ...m,
        { id: userId, role: "user", text },
        { id: botId, role: "assistant", text: "" },
      ])

      const ac = new AbortController()
      abortRef.current = ac
      const patch = (fn: (prev: string) => string) =>
        setMessages((m) => m.map((x) => (x.id === botId ? { ...x, text: fn(x.text) } : x)))

      try {
        for await (const delta of streamChat(text, sessionId, ac.signal)) {
          patch((p) => p + delta)
        }
      } catch (e) {
        if (!ac.signal.aborted) {
          setMessages((m) => [
            ...m.filter((x) => !(x.id === botId && x.text === "")),
            { id: nextId.current++, role: "error", text: e instanceof Error ? e.message : String(e) },
          ])
        } else {
          patch((p) => (p ? p + " ⎯ interrupted" : "interrupted"))
        }
      } finally {
        setBusy(false)
        abortRef.current = null
      }
    },
    [busy, sessionId],
  )

  useKeyboard((key) => {
    if (key.name === "escape" && busy) abortRef.current?.abort()
    if (key.ctrl && key.name === "n") reset()
    if (key.ctrl && key.name === "l") setMessages([])
  })

  // follow the latest message (new message or streamed delta)
  useEffect(() => {
    const toBottom = () => {
      const sb = scrollRef.current
      if (sb) sb.scrollTo(sb.scrollHeight)
    }
    toBottom()
    const h = setTimeout(toBottom, 0) // after markdown re-layout
    return () => clearTimeout(h)
  }, [messages, busy])

  const home = messages.length === 0
  const statusColor = online === null ? t.dim : online ? t.ok : t.err
  const statusText = online === null ? "connecting" : online ? "online" : "offline"

  // ---- prompt box (shared by both screens) -------------------------------
  const prompt = (
    <box
      border={["left"]}
      borderStyle="heavy"
      borderColor={busy ? t.dim : t.accent}
      backgroundColor={t.panel}
      flexDirection="column"
      paddingLeft={1}
      paddingRight={1}
      paddingTop={1}
      paddingBottom={1}
      width={home ? Math.min(76, width - 4) : "100%"}
      flexShrink={0}
    >
      <input
        value={input}
        placeholder={home ? `Ask anything… "What's on YouTube about OpenTUI?"` : "Message Airi…"}
        placeholderColor={t.muted}
        textColor={t.text}
        backgroundColor={t.panel}
        focusedBackgroundColor={t.panel}
        focusedTextColor={t.text}
        focused
        onInput={setInput}
        onSubmit={() => send(input)}
      />
      <box height={1} />
      <box flexDirection="row">
        <text fg={t.accent}>
          <strong>{agent}</strong>
        </text>
        <text fg={t.dim}> · </text>
        <text fg={t.warn}>{MODEL}</text>
        <text fg={t.dim}> · </text>
        <text fg={t.muted}>session {sessionId.slice(0, 8)}</text>
        {busy ? (
          <text fg={t.accent}>
            {"  "}
            {spin} thinking
          </text>
        ) : null}
      </box>
    </box>
  )

  const hints = (
    <box flexDirection="row" justifyContent="flex-end" width={home ? Math.min(76, width - 4) : "100%"} flexShrink={0}>
      {busy ? (
        <>
          <text fg={t.text}>esc</text>
          <text fg={t.muted}> interrupt  </text>
        </>
      ) : null}
      <text fg={t.text}>ctrl+n</text>
      <text fg={t.muted}> new session  </text>
      <text fg={t.text}>ctrl+c</text>
      <text fg={t.muted}> quit</text>
    </box>
  )

  const statusBar = (
    <box flexDirection="row" justifyContent="space-between" width="100%" height={2} flexShrink={0} paddingLeft={1} paddingRight={1}>
      <box flexDirection="row">
        <text fg={statusColor}>● </text>
        <text fg={t.text}>{statusText}</text>
        <text fg={t.dim}> {BASE_URL}</text>
      </box>
      <text fg={t.dim}>{VERSION}</text>
    </box>
  )

  // ---- home screen -------------------------------------------------------
  if (home) {
    return (
      <box flexDirection="column" width="100%" height="100%" backgroundColor={t.bg}>
        <box flexGrow={1} alignItems="center" justifyContent="center" flexDirection="column">
          <box flexDirection="column" alignItems="flex-start" flexShrink={0}>
            {LOGO.map((line, i) => (
              <text key={i} fg={LOGO_COLORS[i]}>
                {line}
              </text>
            ))}
          </box>
          <box height={1} />
          <text fg={t.muted}>
            {agent} · local-first agent
          </text>
          <box height={1} />
          {prompt}
          {hints}
        </box>
        {statusBar}
      </box>
    )
  }

  // ---- chat screen -------------------------------------------------------
  return (
    <box flexDirection="column" width="100%" height="100%" backgroundColor={t.bg}>
      {/* zero-height spacer: without a sibling above it, the scrollbox swallows the prompt/status rows */}
      <box height={0} flexShrink={0} />
      <scrollbox
        ref={scrollRef}
        flexGrow={1}
        stickyScroll
        stickyStart="bottom"
        style={{
          rootOptions: { backgroundColor: t.bg },
          wrapperOptions: { backgroundColor: t.bg },
          viewportOptions: { backgroundColor: t.bg },
          contentOptions: { backgroundColor: t.bg },
        }}
      >
        {messages.map((m) =>
          m.role === "user" ? (
            <box
              key={m.id}
              border={["left"]}
              borderStyle="heavy"
              borderColor={t.accent}
              backgroundColor={t.panel}
              paddingLeft={2}
              paddingRight={2}
              paddingTop={1}
              paddingBottom={1}
              marginTop={1}
              flexShrink={0}
            >
              <text fg={t.text}>{m.text}</text>
            </box>
          ) : m.role === "error" ? (
            <box
              key={m.id}
              border={["left"]}
              borderStyle="heavy"
              borderColor={t.err}
              paddingLeft={1}
              marginTop={1}
              flexShrink={0}
            >
              <text fg={t.err}>{m.text}</text>
            </box>
          ) : (
            <box key={m.id} paddingLeft={2} paddingRight={2} marginTop={1} flexShrink={0}>
              {m.text ? (
                <markdown
                  content={m.text}
                  syntaxStyle={syntaxStyle}
                  fg={t.text}
                  conceal
                  streaming={busy && m.id === messages[messages.length - 1]?.id}
                />
              ) : (
                <text fg={t.accent}>{busy ? spin : ""}</text>
              )}
            </box>
          ),
        )}
      </scrollbox>

      <box flexDirection="column" flexShrink={0} paddingLeft={1} paddingRight={1} marginTop={1}>
        {prompt}
        {hints}
      </box>
      {statusBar}
    </box>
  )
}
