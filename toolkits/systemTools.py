import json
import os
import platform
import shutil
from datetime import datetime
from pathlib import Path

from agno.tools.toolkit import Toolkit


class SystemTools(Toolkit):
    def __init__(self, **kwargs):
        tools = [self.getBattery, self.getSystemInfo, self.getDateTime]
        super().__init__(name="system_tools", tools=tools, **kwargs)

    # ---------- helpers ----------
    @staticmethod
    def _read(path: str) -> str | None:
        try:
            return Path(path).read_text().strip()
        except Exception:
            return None

    # ---------- tools ----------
    def getBattery(self) -> str:
        """Get battery status of the machine: percentage, charging state, and health.
        Returns JSON. Reports no battery on desktops/VMs."""
        supplies = sorted(Path("/sys/class/power_supply").glob("BAT*"))
        if not supplies:
            return json.dumps({"battery": None, "note": "No battery found (desktop or VM)."})

        result = []
        for bat in supplies:
            info = {
                "name": bat.name,
                "percentage": self._read(f"{bat}/capacity"),
                "status": self._read(f"{bat}/status"),  # Charging / Discharging / Full
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

    def getSystemInfo(self) -> str:
        """Get system information: OS, kernel, hostname, CPU, RAM, disk usage, and uptime.
        Returns JSON."""
        # OS name from /etc/os-release
        os_name = platform.system()
        osr = self._read("/etc/os-release")
        if osr:
            for line in osr.splitlines():
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip('"')

        # CPU model
        cpu = platform.processor() or "unknown"
        cpuinfo = self._read("/proc/cpuinfo")
        if cpuinfo:
            for line in cpuinfo.splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break

        # Memory from /proc/meminfo (values in kB)
        mem = {}
        meminfo = self._read("/proc/meminfo")
        if meminfo:
            for line in meminfo.splitlines():
                k, v = line.split(":", 1)
                mem[k] = int(v.strip().split()[0])
        total_gb = round(mem.get("MemTotal", 0) / 1024 / 1024, 2)
        avail_gb = round(mem.get("MemAvailable", 0) / 1024 / 1024, 2)

        # Disk
        du = shutil.disk_usage("/")

        # Uptime
        uptime = self._read("/proc/uptime")
        uptime_s = int(float(uptime.split()[0])) if uptime else None
        uptime_str = None
        if uptime_s is not None:
            d, r = divmod(uptime_s, 86400)
            h, r = divmod(r, 3600)
            m, _ = divmod(r, 60)
            uptime_str = f"{d}d {h}h {m}m"

        return json.dumps(
            {
                "os": os_name,
                "kernel": platform.release(),
                "arch": platform.machine(),
                "hostname": platform.node(),
                "user": os.environ.get("USER"),
                # "python": platform.python_version(),
                "cpu": cpu,
                "cpu_cores": os.cpu_count(),
                "load_avg": [round(x, 2) for x in os.getloadavg()],
                "ram_total_gb": total_gb,
                "ram_available_gb": avail_gb,
                "disk_total_gb": round(du.total / 1024**3, 1),
                "disk_free_gb": round(du.free / 1024**3, 1),
                "uptime": uptime_str,
            }
        )

    def getDateTime(self) -> str:
        """Get the current local date, time, weekday, and timezone of the machine.
        Returns JSON."""
        now = datetime.now().astimezone()
        return json.dumps(
            {
                "iso": now.isoformat(timespec="seconds"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": now.strftime("%A"),
                "timezone": now.tzname(),
                "utc_offset": now.strftime("%z"),
            }
        )
