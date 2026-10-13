import { RGBA, SyntaxStyle } from "@opentui/core"
import type { CliRenderer } from "@opentui/core"
import { theme as t } from "./theme"

const c = (hex: string) => RGBA.fromHex(hex)

export function createMarkdownStyle(renderer: CliRenderer) {
  return SyntaxStyle.fromStyles(
    {
      default: { fg: c(t.text) },
      "markup.heading": { fg: c(t.accent), bold: true },
      "markup.strong": { fg: c("#f4f4f5"), bold: true },
      "markup.italic": { fg: c(t.text), italic: true },
      "markup.strikethrough": { fg: c(t.muted) },
      "markup.raw": { fg: c(t.warn) },
      "markup.raw.block": { fg: c(t.text) },
      "markup.list": { fg: c(t.accent) },
      "markup.quote": { fg: c(t.muted), italic: true },
      "markup.link": { fg: c(t.link) },
      "markup.link.label": { fg: c(t.link), underline: true },
      "markup.link.url": { fg: c(t.muted) },
      conceal: { fg: c(t.dim) },
      label: { fg: c(t.warn) },
      keyword: { fg: c("#c084fc"), bold: true },
      string: { fg: c(t.ok) },
      comment: { fg: c(t.muted), italic: true },
      number: { fg: c(t.warn) },
      function: { fg: c(t.link) },
    },
    renderer.nativeScene!,
  )
}
