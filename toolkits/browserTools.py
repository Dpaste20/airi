import json
import os
import shlex
import subprocess

from agno.tools.toolkit import Toolkit

MAX_OUTPUT = 12_000  # chars returned to the model (head + tail kept)

# flags that change launch/file/policy behaviour: not for the model to set
BLOCKED_FLAGS = {
    "--executable-path", "--args", "--extension", "--init-script", "--config",
    "--allow-file-access", "--cdp", "--auto-connect", "--session", "--provider", "-p",
    "--profile", "--proxy", "--action-policy",
}

GUIDE = """\
Control the user's Chrome with the agent-browser CLI. Call agent_browser(command) with the command WITHOUT the
'agent-browser' prefix. Chrome is attached over CDP automatically. Workflow: open -> snapshot -i -> act on @eN refs
-> snapshot -i again (refs go stale after the page changes).

  open <url>                    navigate          back | forward | reload
  snapshot -i                   interactive elements with @eN refs
  read                          readable text of the current page
  click @e3                     fill @e4 "text"   type @e4 "text"   press Enter
  scroll down 600               hover @e2         select @e5 "label"   check @e6
  get text @e3 | get url | get title
  wait --text "Welcome" | wait --load domcontentloaded | wait 1000
  tab | tab t2 | tab new <url> | tab close
  screenshot [path]             eval "<js>"       find text "Sign in" click
Quote arguments with spaces. Never enter passwords or payment details."""


class BrowserTools(Toolkit):
    """Single-command agent-browser tool, shaped like BashTools: agent_browser(command).

    Launch Chrome yourself first, e.g.
        google-chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.airi-chrome [--no-sandbox]
    First call runs `connect <port>` (fallback: --auto-connect); the attachment then persists
    for the session, so later commands need no flags.
    """

    def __init__(self, cdp: str | None = None, session: str = "airi", binary: str | None = None,
                 default_timeout: int = 60, **kwargs):
        self.cdp = cdp or os.environ.get("AGENT_BROWSER_CDP", "9222")
        self.session = session
        self.binary = binary or os.environ.get("AGENT_BROWSER", "agent-browser")
        self.default_timeout = default_timeout
        self._connected = False
        super().__init__(
            name="browser_tools",
            tools=[self.agent_browser],
            instructions=GUIDE,
            add_instructions=True,
            **kwargs,
        )

    @staticmethod
    def _truncate(s: str) -> str:
        if len(s) <= MAX_OUTPUT:
            return s
        half = MAX_OUTPUT // 2
        return f"{s[:half]}\n...[{len(s) - MAX_OUTPUT} chars truncated]...\n{s[-half:]}"

    def _exec(self, args: list[str], global_args: list[str] | None = None, timeout: int | None = None):
        return subprocess.run(
            [self.binary, "--session", self.session, *(global_args or []), *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout or self.default_timeout,
        )

    def _connect(self) -> str | None:
        if self._connected:
            return None
        errs = []
        try:
            p = self._exec(["connect", self.cdp], timeout=30)
            if p.returncode == 0:
                self._connected = True
                return None
            errs.append(f"connect {self.cdp}: {(p.stdout + p.stderr).strip()[:300]}")
        except Exception as e:
            errs.append(f"connect {self.cdp}: {e}")
        try:  # Chrome 144+ may show a permission prompt
            p = self._exec(["get", "url"], global_args=["--auto-connect"], timeout=45)
            if p.returncode == 0:
                self._connected = True
                return None
            errs.append(f"auto-connect: {(p.stdout + p.stderr).strip()[:300]}")
        except Exception as e:
            errs.append(f"auto-connect: {e}")
        return " | ".join(errs) + " -- start Chrome with --remote-debugging-port=9222 --user-data-dir=$HOME/.airi-chrome"

    def agent_browser(self, command: str, timeout: int | None = None) -> str:
        """Run one agent-browser command against the user's Chrome and return JSON with ok, output, error.
        Pass the command without the 'agent-browser' prefix, e.g. 'open https://example.com',
        'snapshot -i', 'click @e3', 'fill @e4 "hello"', 'press Enter', 'read'."""
        try:
            args = shlex.split(command)
        except ValueError as e:
            return json.dumps({"ok": False, "error": f"bad quoting: {e}"})
        if args and args[0] == "agent-browser":
            args = args[1:]
        if not args:
            return json.dumps({"ok": False, "error": "empty command"})
        bad = next((a.split("=")[0] for a in args if a.split("=")[0] in BLOCKED_FLAGS), None)
        if bad:
            return json.dumps({"ok": False, "error": f"flag not allowed: {bad}"})
        if args[0] in ("close", "quit", "exit"):
            self._connected = False  # detach; next call reconnects

        err = self._connect() if args[0] not in ("close", "quit", "exit") else None
        if err:
            return json.dumps({"ok": False, "error": err})
        try:
            p = self._exec(args, timeout=timeout)
        except subprocess.TimeoutExpired:
            return json.dumps({"ok": False, "error": f"Timed out after {timeout or self.default_timeout}s"})
        except FileNotFoundError:
            return json.dumps({"ok": False, "error": f"{self.binary} not found (npm i -g agent-browser)"})
        out, er = (p.stdout or "").strip(), (p.stderr or "").strip()
        if p.returncode != 0 and "connect" in er.lower():
            self._connected = False
        res = {"ok": p.returncode == 0, "output": self._truncate(out)}
        if er and p.returncode != 0:
            res["error"] = self._truncate(er)
        return json.dumps(res)
