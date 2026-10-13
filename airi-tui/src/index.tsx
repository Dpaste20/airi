import { createCliRenderer } from "@opentui/core"
import { createRoot } from "@opentui/react"
import { App } from "./app"
import { createMarkdownStyle } from "./markdown-theme"

const renderer = await createCliRenderer({ exitOnCtrlC: true })
const syntaxStyle = createMarkdownStyle(renderer)

try {
  createRoot(renderer).render(<App syntaxStyle={syntaxStyle} />)
  await renderer.closed
} finally {
  renderer.destroy()
  syntaxStyle.destroy()
}
