/** Open the OS-native file dialog. Resolves to selected paths ([] if cancelled). */

async function exec(cmd: string[]): Promise<{ out: string; err: string; code: number }> {
  const proc = Bun.spawn(cmd, { stdin: "ignore", stdout: "pipe", stderr: "pipe" })
  const [out, err, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ])
  return { out, err: err.trim(), code }
}

const lines = (s: string) =>
  s
    .split("\n")
    .map((l) => l.replace(/\r$/, "").trim())
    .filter(Boolean)

function result(r: { out: string; err: string; code: number }): string[] {
  const paths = lines(r.out)
  if (paths.length) return paths
  // empty output: user cancelled (zenity/kdialog exit 1, osascript -128) unless it really failed
  if (r.code > 1 && r.err && !r.err.includes("-128")) throw new Error(`file picker: ${r.err.slice(0, 200)}`)
  return []
}

export async function pickFiles(): Promise<string[]> {
  switch (process.platform) {
    case "darwin":
      return result(
        await exec([
          "osascript",
          "-e", 'set fs to choose file with prompt "Attach files" with multiple selections allowed',
          "-e", 'set out to ""',
          "-e", "repeat with f in fs",
          "-e", "set out to out & POSIX path of f & linefeed",
          "-e", "end repeat",
          "-e", "return out",
        ]),
      )

    case "win32":
      return result(
        await exec([
          "powershell",
          "-NoProfile",
          "-STA",
          "-Command",
          "Add-Type -AssemblyName System.Windows.Forms; " +
            "$d = New-Object System.Windows.Forms.OpenFileDialog; " +
            "$d.Multiselect = $true; $d.Title = 'Attach files'; " +
            "if ($d.ShowDialog() -eq 'OK') { $d.FileNames }",
        ]),
      )

    default: {
      if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
        throw new Error("no display for a file picker (SSH?). Use /attachfile <path>")
      }
      if (Bun.which("zenity")) {
        return result(
          await exec(["zenity", "--file-selection", "--multiple", "--separator=\n", "--title=Attach files"]),
        )
      }
      if (Bun.which("kdialog")) {
        return result(
          await exec(["kdialog", "--getopenfilename", process.env.HOME ?? "/", "--multiple", "--separate-output", "--title", "Attach files"]),
        )
      }
      throw new Error("no file picker found: install zenity or kdialog, or use /attachfile <path>")
    }
  }
}
