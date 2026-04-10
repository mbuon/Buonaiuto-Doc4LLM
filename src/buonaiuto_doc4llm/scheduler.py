"""Daily documentation fetch scheduler using macOS launchd or crontab."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

LAUNCHD_LABEL = "com.buonaiuto-doc4llm.daily-fetch"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"


def _python_bin() -> str:
    return sys.executable


def _module_args(base_dir: Path) -> list[str]:
    return [
        "-m", "buonaiuto_doc4llm",
        "--base-dir", str(base_dir),
        "fetch",
    ]


def _src_dir() -> str:
    return str(Path(__file__).resolve().parents[1])


# ------------------------------------------------------------------
# launchd (macOS)
# ------------------------------------------------------------------

def _plist_path() -> Path:
    return PLIST_DIR / f"{LAUNCHD_LABEL}.plist"


def _build_plist(base_dir: Path, hour: int, minute: int) -> str:
    from html import escape as xml_escape
    python = _python_bin()
    src = _src_dir()
    args_xml = "\n".join(
        f"        <string>{xml_escape(str(a))}</string>"
        for a in [python] + _module_args(base_dir)
    )
    log_dir = base_dir / "state" / "logs"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>{xml_escape(str(src))}</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>{xml_escape(str(log_dir / "daily-fetch.stdout.log"))}</string>
    <key>StandardErrorPath</key>
    <string>{xml_escape(str(log_dir / "daily-fetch.stderr.log"))}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def install_schedule(base_dir: Path, hour: int = 4, minute: int = 0) -> dict[str, Any]:
    """Install a daily fetch schedule.

    On macOS, installs a launchd agent.
    On Linux, installs a crontab entry.

    Returns a summary dict.
    """
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be 0-23, got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"minute must be 0-59, got {minute}")

    if platform.system() == "Darwin":
        return _install_launchd(base_dir, hour, minute)
    return _install_crontab(base_dir, hour, minute)


def uninstall_schedule() -> dict[str, Any]:
    """Remove the daily fetch schedule."""
    if platform.system() == "Darwin":
        return _uninstall_launchd()
    return _uninstall_crontab()


def schedule_status() -> dict[str, Any]:
    """Check if the daily fetch schedule is installed."""
    if platform.system() == "Darwin":
        return _launchd_status()
    return _crontab_status()


def _install_launchd(base_dir: Path, hour: int, minute: int) -> dict[str, Any]:
    # Ensure log directory exists
    log_dir = base_dir / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    plist = _plist_path()

    # Unload existing if present
    if plist.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist)],
            capture_output=True,
        )

    plist.write_text(_build_plist(base_dir, hour, minute), encoding="utf-8")
    subprocess.run(["launchctl", "load", str(plist)], check=True, capture_output=True)

    return {
        "installed": True,
        "method": "launchd",
        "plist": str(plist),
        "schedule": f"daily at {hour:02d}:{minute:02d}",
        "log_dir": str(log_dir),
    }


def _uninstall_launchd() -> dict[str, Any]:
    plist = _plist_path()
    if not plist.exists():
        return {"uninstalled": False, "reason": "not_installed"}

    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    plist.unlink()
    return {"uninstalled": True, "method": "launchd", "plist": str(plist)}


def _launchd_status() -> dict[str, Any]:
    plist = _plist_path()
    if not plist.exists():
        return {"installed": False}

    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL],
        capture_output=True, text=True,
    )
    loaded = result.returncode == 0
    return {
        "installed": True,
        "loaded": loaded,
        "method": "launchd",
        "plist": str(plist),
    }


# ------------------------------------------------------------------
# crontab (Linux)
# ------------------------------------------------------------------

_CRON_MARKER = f"# {LAUNCHD_LABEL}"


def _cron_line(base_dir: Path, hour: int, minute: int) -> str:
    import shlex
    python = shlex.quote(str(_python_bin()))
    src = shlex.quote(str(_src_dir()))
    args = " ".join(shlex.quote(str(a)) for a in _module_args(base_dir))
    return f"{minute} {hour} * * * PYTHONPATH={src} {python} {args} {_CRON_MARKER}"


def _install_crontab(base_dir: Path, hour: int, minute: int) -> dict[str, Any]:
    _remove_cron_entry()
    line = _cron_line(base_dir, hour, minute)
    existing = _read_crontab()
    new_crontab = existing.rstrip("\n") + "\n" + line + "\n" if existing.strip() else line + "\n"
    subprocess.run(
        ["crontab", "-"],
        input=new_crontab, text=True, check=True, capture_output=True,
    )
    return {
        "installed": True,
        "method": "crontab",
        "schedule": f"daily at {hour:02d}:{minute:02d}",
        "entry": line,
    }


def _uninstall_crontab() -> dict[str, Any]:
    removed = _remove_cron_entry()
    if not removed:
        return {"uninstalled": False, "reason": "not_installed"}
    return {"uninstalled": True, "method": "crontab"}


def _crontab_status() -> dict[str, Any]:
    existing = _read_crontab()
    installed = _CRON_MARKER in existing
    return {"installed": installed, "method": "crontab"}


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def _remove_cron_entry() -> bool:
    existing = _read_crontab()
    if _CRON_MARKER not in existing:
        return False
    lines = [l for l in existing.splitlines() if _CRON_MARKER not in l]
    new_crontab = "\n".join(lines) + "\n" if lines else ""
    subprocess.run(
        ["crontab", "-"],
        input=new_crontab, text=True, check=True, capture_output=True,
    )
    return True
