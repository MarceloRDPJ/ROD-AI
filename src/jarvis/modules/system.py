import psutil
import subprocess
import asyncio
import re
import shutil
import time
from datetime import timedelta
from typing import Dict, Any

START_TIME = time.time()


class SystemModule:
    """
    SystemModule

    RESPONSABILIDADES:
    - Coleta RAW
    - Status HUMANO
    """

    # ==================================================
    # RAW SYSTEM DATA (PASSO 5)
    # ==================================================
    @staticmethod
    async def get_raw_status() -> Dict[str, Any]:
        return await asyncio.to_thread(SystemModule._get_raw_status_sync)

    @staticmethod
    def _get_raw_status_sync() -> Dict[str, Any]:
        # 200 ms mantém a leitura real sem fazer cada comando "status" esperar 1 s.
        cpu_percent = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        now = time.time()
        try:
            boot_time = float(psutil.boot_time())
        except Exception:
            boot_time = START_TIME
        try:
            load_average = [round(value, 2) for value in psutil.getloadavg()]
        except (AttributeError, OSError):
            load_average = None

        return {
            "timestamp": now,
            "host_boot_time": boot_time,
            "uptime_seconds": max(0, int(now - boot_time)),
            "service_uptime_seconds": max(0, int(now - START_TIME)),
            "cpu_percent": round(cpu_percent, 1),
            "cpu_count": psutil.cpu_count(logical=True),
            "load_average": load_average,
            "memory": {
                "percent": round(mem.percent, 1),
                "used": mem.used,
                "available": mem.available,
                "total": mem.total,
            },
            "swap": {
                "percent": round(swap.percent, 1),
                "used": swap.used,
                "free": swap.free,
                "total": swap.total,
            },
            "disk": {
                "path": "/",
                "percent": round(disk.percent, 1),
                "used": disk.used,
                "free": disk.free,
                "total": disk.total,
            },
            "temperature_c": SystemModule._read_temperature(),
            "throttling": SystemModule._read_throttling(),
        }

    @staticmethod
    def _read_temperature() -> float | None:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", encoding="ascii") as value:
                return round(int(value.read().strip()) / 1000, 1)
        except (OSError, ValueError):
            executable = shutil.which("vcgencmd")
            if not executable:
                return None
            try:
                result = subprocess.run(
                    [executable, "measure_temp"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=2,
                )
                match = re.search(r"(-?\d+(?:\.\d+)?)", result.stdout)
                return round(float(match.group(1)), 1) if match else None
            except (OSError, ValueError, subprocess.SubprocessError):
                return None

    @staticmethod
    def _read_throttling() -> Dict[str, Any] | None:
        """Decode Raspberry Pi undervoltage/throttling flags when vcgencmd is available."""
        executable = shutil.which("vcgencmd")
        if not executable:
            return None
        try:
            result = subprocess.run(
                [executable, "get_throttled"],
                capture_output=True,
                text=True,
                check=True,
                timeout=2,
            )
            match = re.search(r"0x([0-9a-fA-F]+)", result.stdout)
            if not match:
                return None
            flags = int(match.group(1), 16)
            active_bits = {
                0: "undervoltage",
                1: "frequency_capped",
                2: "throttled",
                3: "soft_temperature_limit",
            }
            occurred_bits = {
                16: "undervoltage",
                17: "frequency_capped",
                18: "throttled",
                19: "soft_temperature_limit",
            }
            return {
                "raw": f"0x{flags:x}",
                "active": [label for bit, label in active_bits.items() if flags & (1 << bit)],
                "occurred_since_boot": [
                    label for bit, label in occurred_bits.items() if flags & (1 << bit)
                ],
            }
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    # ==================================================
    # HUMANO — STATUS PARA CHAT
    # ==================================================
    @staticmethod
    async def get_status() -> str:
        raw = await SystemModule.get_raw_status()

        temp = (
            f"{raw['temperature_c']:.1f} °C"
            if raw["temperature_c"] is not None
            else "N/A"
        )

        uptime = str(timedelta(seconds=raw["uptime_seconds"]))
        available_mib = raw["memory"]["available"] / (1024 * 1024)
        free_gib = raw["disk"]["free"] / (1024 * 1024 * 1024)
        throttle = raw.get("throttling") or {}
        throttle_line = ""
        if throttle.get("active"):
            throttle_line = f"\n- Alerta: {', '.join(throttle['active'])}"

        return (
            "🖥️ *Status do Sistema*\n"
            f"- CPU: {raw['cpu_percent']:.1f}%\n"
            f"- RAM: {raw['memory']['percent']:.1f}% ({available_mib:.0f} MiB livres)\n"
            f"- Disco: {raw['disk']['percent']:.1f}% ({free_gib:.1f} GiB livres)\n"
            f"- Temp: {temp}\n"
            f"- Uptime do Pi: {uptime}"
            f"{throttle_line}"
        )

    # ==================================================
    # HEALTHCHECK
    # ==================================================
    @staticmethod
    async def get_health() -> str:
        uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
        return f"✅ Healthcheck OK\nUptime: {uptime}"
