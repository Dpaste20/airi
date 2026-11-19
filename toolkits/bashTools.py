import json
import os
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

from agno.tools.toolkit import Toolkit

MAX_OUTPUT = 10_000  # chars returned to the model (head + tail kept)
DEFAULT_WORKSPACE = "~/airi-workspace"
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Optional hard memory cap via systemd, e.g. AIRI_MEM_MAX=4G (needs systemd --user)
MEM_MAX = os.environ.get("AIRI_MEM_MAX")

# Readable system-wide, except credentials. Paths are relative to $HOME unless absolute.
# Extend with AIRI_HIDE=".env:projects/secret:/opt/keys" (colon-separated).
SECRET_PATHS = [
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc", ".git-credentials",
    ".pgpass", ".npmrc", ".pypirc", ".password-store", ".bash_history", ".zsh_history",
    ".config/gcloud", ".config/gh", ".config/google-chrome", ".config/chromium",
    ".config/BraveSoftware", ".mozilla", ".local/share/keyrings",
    "snap/firefox/common/.mozilla", "snap/chromium/common/chromium",
]


class SandboxError(RuntimeError):
    pass


class BashTools(Toolkit):
    """Bash inside a bubblewrap sandbox.

    - whole filesystem READABLE (read-only), incl. /home, /mnt, /media; /run hidden (no sockets)
    - credential paths (SECRET_PATHS + AIRI_HIDE) masked with empty tmpfs
    - only `workspace` is writable (bound at the same path)
    - network shared with host (needed for pip/curl); every other namespace unshared
    - env cleared; HOME is the workspace
    """

    def __init__(self, workspace: str | None = None, default_timeout: int = 30, **kwargs):
        self.workspace = Path(workspace or os.environ.get("AIRI_WORKSPACE", DEFAULT_WORKSPACE)).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cwd = str(self.workspace)
        self.default_timeout = default_timeout
        self._check_sandbox()  # refuse to exist unsandboxed

        super().__init__(
            name="bash_tools",
            tools=[self.bash],
            instructions=(
                "Use bash to run shell commands inside a sandbox. "
                "You can read, search and locate files anywhere on the system (find, grep, rg, locate, cat, ls); "
                "credential folders (.ssh, .aws, browser profiles, ...) are hidden. "
                f"Only the workspace ({self.workspace}) is writable; everything else is read-only. HOME is the workspace. "
                "For whole-system searches prune /proc /sys /dev, pass a larger `timeout`, and narrow with -name/-path/-maxdepth. "
                "The network is available. System-wide installs are impossible: use `python -m venv` or "
                "`pip install --user` for Python and local installs for other tools. "
                "The working directory persists between calls but resets to the workspace if it leaves it; use absolute paths elsewhere. "
                "Commands are non-interactive (no stdin), have no access to desktop/audio/D-Bus, and time out. "
                "Prefer short, targeted commands and avoid destructive ones unless the user explicitly asked."
            ),
            add_instructions=True,
            **kwargs,
        )

    # ---------- sandbox ----------
    def _bwrap_cmd(self, cwd: str) -> list[str]:
        ws = str(self.workspace)
        # user-level toolchains (bun/bunx) live in the real home, which is readable read-only
        extra = [str(Path.home() / ".bun/bin"), *[x for x in os.environ.get("AIRI_EXTRA_PATH", "").split(":") if x]]
        path = ":".join([*extra, SANDBOX_PATH])
        return [
            "bwrap",
            "--ro-bind", "/", "/",
            "--tmpfs", "/run",  # no D-Bus / Pulse / Docker sockets
            "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",  # keep DNS working
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            *self._mask_args(),
            # the one writable dir
            "--bind", ws, ws,
            "--unshare-all", "--share-net",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--setenv", "PATH", path,
            "--setenv", "HOME", ws,
            "--setenv", "LANG", "C.UTF-8",
            "--setenv", "TERM", "dumb",
            "--chdir", cwd,
        ]

    def _mask_args(self) -> list[str]:
        home = Path.home()
        extra = [x for x in os.environ.get("AIRI_HIDE", "").split(":") if x]
        args: list[str] = []
        for raw in [*SECRET_PATHS, *extra]:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = home / path
            path = path.resolve()
            if self.workspace == path or self.workspace.is_relative_to(path):
                continue
            if path.is_dir():
                args += ["--tmpfs", str(path)]
            elif path.exists():
                args += ["--ro-bind", "/dev/null", str(path)]
        return args

    def _check_sandbox(self) -> None:
        if shutil.which("bwrap") is None:
            raise SandboxError("bwrap not found. Install it: sudo apt install bubblewrap")
        try:
            r = subprocess.run(
                [*self._bwrap_cmd(str(self.workspace)), "true"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            raise SandboxError("bwrap self-test timed out")
        if r.returncode != 0:
            hint = ""
            if "uid map" in r.stderr or "Permission denied" in r.stderr:
                hint = (
                    "\nUbuntu 24.04+ blocks unprivileged user namespaces. Fix: add an AppArmor profile for "
                    "/usr/bin/bwrap, or `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`."
                )
            raise SandboxError(f"bwrap self-test failed: {r.stderr.strip()}{hint}")

    def _inside_workspace(self, p: str) -> bool:
        try:
            return Path(os.path.realpath(p)).is_relative_to(self.workspace)
        except Exception:
            return False

    # ---------- helpers ----------
    @staticmethod
    def _truncate(s: str) -> str:
        if len(s) <= MAX_OUTPUT:
            return s
        half = MAX_OUTPUT // 2
        return f"{s[:half]}\n...[{len(s) - MAX_OUTPUT} chars truncated]...\n{s[-half:]}"

    # ---------- tool ----------
    def bash(self, command: str, timeout: int | None = None) -> str:
        """Run a bash command in a sandbox and return JSON with stdout, stderr, exit_code and cwd.
        Only the workspace is writable. The working directory persists across calls.
        Non-interactive; killed after `timeout` seconds (default 30)."""
        timeout = timeout or self.default_timeout
        marker = f"__CWD_{uuid.uuid4().hex}__"
        # ulimit -f: cap single-file size at 1 GiB (1 KiB blocks). cwd is printed after a marker so `cd` persists.
        script = f'ulimit -f 1048576\n{command}\n__rc=$?\necho "{marker}$(pwd)"\nexit $__rc'

        prefix: list[str] = []
        if MEM_MAX and shutil.which("systemd-run"):
            prefix = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={MEM_MAX}", "-p", "MemorySwapMax=0"]

        proc = subprocess.Popen(
            [*prefix, *self._bwrap_cmd(self.cwd), "bash", "-c", script],
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
            if new_cwd and Path(new_cwd).is_dir() and self._inside_workspace(new_cwd):
                self.cwd = new_cwd
            else:
                self.cwd = str(self.workspace)  # tried to leave the workspace → reset

        return json.dumps(
            {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": self._truncate(out),
                "stderr": self._truncate(err),
                "cwd": self.cwd,
            }
        )
