import json
import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from agno.tools.toolkit import Toolkit


class SystemTools(Toolkit):
    def __init__(self, **kwargs):
        self.pending_power: str | None = None  # "poweroff" | "reboot" | "suspend"
        tools = [
            self.getProcesses,
            self.getBattery,
            self.getNetwork,
            self.getStorage,
            self.getAudio,
            self.getDisplay,
            self.getBluetooth,
            self.getSystemSettings,
            self.getLogs,
            self.shutdown,
            self.restart,
            self.sleep,
        ]
        super().__init__(name="system_tools", tools=tools, **kwargs)

    # ---------- helpers ----------
    @staticmethod
    def _read(path: str) -> str | None:
        try:
            return Path(path).read_text().strip()
        except Exception:
            return None

    @staticmethod
    def _run_cmd(cmd: list[str]) -> str | None:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return None

    @staticmethod
    def _power_cmd(action: str) -> list[str] | None:
        cmds = {
            "poweroff": [["systemctl", "poweroff"], ["shutdown", "-h", "now"]],
            "reboot": [["systemctl", "reboot"], ["shutdown", "-r", "now"]],
            "suspend": [["systemctl", "suspend"], ["pm-suspend"]],
        }[action]
        return next((c for c in cmds if shutil.which(c[0])), None)

    def _schedule(self, action: str, label: str) -> str:
        if self._power_cmd(action) is None:
            return json.dumps({"ok": False, "error": f"No {label} command found."})
        self.pending_power = action
        return json.dumps(
            {
                "ok": True,
                "action": label,
                "message": f"{label.capitalize()} scheduled. It will run 3 seconds after your reply is finished. Briefly tell the user, then end your reply.",
            }
        )

    def clear_pending(self) -> None:
        self.pending_power = None

    def run_pending(self, delay: int = 3) -> str | None:
        action, self.pending_power = self.pending_power, None
        if action is None:
            return None
        cmd = self._power_cmd(action)
        if cmd is None:
            return None
        subprocess.Popen(
            ["sh", "-c", f"sleep {delay}; exec {' '.join(cmd)}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return action

    # ---------- tools ----------
    def getProcesses(self) -> str:
        """Get the top 10 active processes consuming the most CPU and Memory. Returns JSON."""
        output = self._run_cmd(["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"])
        if not output:
            return json.dumps({"error": "Failed to retrieve process list."})

        lines = output.splitlines()
        processes = []
        for line in lines[1:11]:  # Skip header, get top 10
            parts = line.split(maxsplit=3)
            if len(parts) == 4:
                processes.append({
                    "pid": parts[0],
                    "command": parts[1],
                    "cpu_percent": parts[2],
                    "mem_percent": parts[3]
                })
        return json.dumps({"top_processes": processes})

    def getBattery(self) -> str:
        """Get battery status: percentage, charging state, health, and AC status. Returns JSON."""
        supplies = sorted(Path("/sys/class/power_supply").glob("BAT*"))
        if not supplies:
            return json.dumps({"battery": None, "note": "No battery found (desktop or VM)."})

        result = []
        for bat in supplies:
            info = {
                "name": bat.name,
                "percentage": self._read(f"{bat}/capacity"),
                "status": self._read(f"{bat}/status"),
            }
            full = self._read(f"{bat}/energy_full") or self._read(f"{bat}/charge_full")
            design = self._read(f"{bat}/energy_full_design") or self._read(f"{bat}/charge_full_design")
            if full and design and design.isdigit() and int(design) > 0:
                info["health_percent"] = round(int(full) / int(design) * 100, 1)
            result.append(info)

        ac = next(
            (p for p in Path("/sys/class/power_supply").iterdir() if self._read(f"{p}/type") == "Mains"),
            None,
        )
        plugged = self._read(f"{ac}/online") == "1" if ac else None
        return json.dumps({"batteries": result, "ac_plugged_in": plugged})

    def getNetwork(self) -> str:
        """Get network interfaces, IP addresses, and operational states. Returns JSON."""
        output = self._run_cmd(["ip", "-j", "addr"])
        if not output:
            return json.dumps({"error": "Failed to retrieve network interfaces."})
        try:
            interfaces = json.loads(output)
            parsed_interfaces = []
            for iface in interfaces:
                ipv4 = next((addr["local"] for addr in iface.get("addr_info", []) if addr.get("family") == "inet"), None)
                parsed_interfaces.append({
                    "interface": iface.get("ifname"),
                    "state": iface.get("operstate"),
                    "ipv4": ipv4
                })
            return json.dumps({"interfaces": parsed_interfaces})
        except json.JSONDecodeError:
            return json.dumps({"error": "Failed to parse network data."})

    def getStorage(self) -> str:
        """Get storage details, total size, used, and free space for primary partitions. Returns JSON."""
        output = self._run_cmd(["df", "-h", "-T", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"])
        if not output:
            return json.dumps({"error": "Failed to retrieve storage information."})

        lines = output.splitlines()
        storage_drives = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 7:
                storage_drives.append({
                    "filesystem": parts[0],
                    "type": parts[1],
                    "total_size": parts[2],
                    "used": parts[3],
                    "available": parts[4],
                    "use_percentage": parts[5],
                    "mount_point": parts[6]
                })
        return json.dumps({"storage": storage_drives})

    def getAudio(self) -> str:
        """Get current audio volume level and mute status. Returns JSON."""
        output = self._run_cmd(["amixer", "sget", "Master"])
        if not output:
            return json.dumps({"error": "Failed to retrieve audio status. amixer may not be installed or configured."})

        volume = None
        state = None
        for line in output.splitlines():
            if "[" in line and "%]" in line:
                vol_start = line.find("[") + 1
                vol_end = line.find("%]")
                volume = line[vol_start:vol_end]
                state = "off" if "[off]" in line else "on"
                break

        return json.dumps({"volume_percent": volume, "state": state})

    def getDisplay(self) -> str:
        """Get connected displays and current resolutions. Returns JSON."""
        output = self._run_cmd(["xrandr", "--listmonitors"])
        if not output:
            return json.dumps({"error": "Failed to retrieve display information. Ensure xrandr is available."})

        lines = output.splitlines()
        monitors = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 4:
                monitors.append({
                    "name": parts[-1],
                    "resolution": parts[2].split("/")[0]
                })
        return json.dumps({"displays": monitors})

    def getBluetooth(self) -> str:
        """Get a list of paired and connected Bluetooth devices. Returns JSON."""
        output = self._run_cmd(["bluetoothctl", "devices"])
        if not output:
            return json.dumps({"error": "Bluetooth service unreachable or bluetoothctl not installed."})

        devices = []
        for line in output.splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                devices.append({
                    "mac_address": parts[1],
                    "name": parts[2]
                })
        return json.dumps({"paired_devices": devices})

    def getSystemSettings(self) -> str:
        """Get general system information, OS details, uptime, and current timezone. Returns JSON."""
        os_name = platform.system()
        osr = self._read("/etc/os-release")
        if osr:
            for line in osr.splitlines():
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip('"')

        uptime = self._read("/proc/uptime")
        uptime_str = None
        if uptime:
            uptime_s = int(float(uptime.split()[0]))
            d, r = divmod(uptime_s, 86400)
            h, r = divmod(r, 3600)
            m, _ = divmod(r, 60)
            uptime_str = f"{d}d {h}h {m}m"

        now = datetime.now().astimezone()

        return json.dumps({
            "os": os_name,
            "kernel": platform.release(),
            "arch": platform.machine(),
            "hostname": platform.node(),
            "uptime": uptime_str,
            "datetime": {
                "iso": now.isoformat(timespec="seconds"),
                "timezone": now.tzname(),
            }
        })

    def getLogs(self) -> str:
        """Get the last 20 system log entries for diagnostics. Returns JSON."""
        output = self._run_cmd(["journalctl", "-n", "20", "--no-pager", "-o", "short-iso"])
        if not output:
            return json.dumps({"error": "Failed to retrieve system logs."})

        return json.dumps({"recent_logs": output.splitlines()})

    # ---------- power state tools ----------
    def shutdown(self) -> str:
        """Shut down the machine. Returns JSON."""
        return self._schedule("poweroff", "shutdown")

    def restart(self) -> str:
        """Restart (reboot) the machine. Returns JSON."""
        return self._schedule("reboot", "restart")

    def sleep(self) -> str:
        """Put the machine to sleep (suspend to RAM). Returns JSON."""
        return self._schedule("suspend", "sleep")
