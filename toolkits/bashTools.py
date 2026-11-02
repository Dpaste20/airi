import json
import os
import signal
import subprocess
import uuid
from pathlib import Path

from agno.tools.toolkit import Toolkit

MAX_OUTPUT = 10_000  # chars returned to the model (head + tail kept)


class BashTools(Toolkit):
    def __init__(self, cwd: str | None = None, default_timeout: int = 30, **kwargs):
        self.cwd = str(Path(cwd or Path.home()).expanduser().resolve())
        self.default_timeout = default_timeout
        super().__init__(
            name="bash_tools",
            tools=[self.bash],
            instructions=(
                "Use bash to run shell commands. The working directory persists between calls. "
                "Commands are non-interactive (no stdin) and time out. Prefer short, targeted commands "
                "and avoid destructive ones unless the user explicitly asked."
            ),
            add_instructions=True,
            **kwargs,
        )

    @staticmethod
    def _truncate(s: str) -> str:
        if len(s) <= MAX_OUTPUT:
            return s
        half = MAX_OUTPUT // 2
        return f"{s[:half]}\n...[{len(s) - MAX_OUTPUT} chars truncated]...\n{s[-half:]}"

    def bash(self, command: str, timeout: int | None = None) -> str:
        """Run a bash command and return JSON with stdout, stderr, exit_code and cwd.
        The working directory persists across calls. Non-interactive; killed after `timeout` seconds
        (default 30)."""
        timeout = timeout or self.default_timeout
        marker = f"__CWD_{uuid.uuid4().hex}__"
        # run the command, then print the final cwd after a marker so `cd` persists
        script = f'{command}\n__rc=$?\necho "{marker}$(pwd)"\nexit $__rc'

        proc = subprocess.Popen(
            ["bash", "-c", script],
            cwd=self.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # own process group so we can kill children on timeout
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            return json.dumps(
                {
                    "ok": False,
                    "error": f"Timed out after {timeout}s",
                    "stdout": self._truncate(out.split(marker)[0]),
                    "stderr": self._truncate(err),
                    "cwd": self.cwd,
                }
            )

        if marker in out:
            out, _, new_cwd = out.rpartition(marker)
            new_cwd = new_cwd.strip()
            if new_cwd and Path(new_cwd).is_dir():
                self.cwd = new_cwd

        return json.dumps(
            {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": self._truncate(out),
                "stderr": self._truncate(err),
                "cwd": self.cwd,
            }
        )
