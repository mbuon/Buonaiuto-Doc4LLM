"""Documentation fetch scheduler using macOS launchd or crontab.

Two schedule entries are installed together:
  1. daily-fetch — daily at HH:MM (default 04:00), runs `fetch --all`
  2. refresh-active — every 3 days at HH:(MM+15), runs `refresh-active`
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

LAUNCHD_LABEL_FETCH = "com.buonaiuto-doc4llm.daily-fetch"
LAUNCHD_LABEL_REFRESH = "com.buonaiuto-doc4llm.refresh-active"
# Back-compat alias for pre-existing callers (e.g. tests/test_scheduler.py)
LAUNCHD_LABEL = LAUNCHD_LABEL_FETCH
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"

# 3 days in seconds — launchd StartInterval for the refresh job
REFRESH_INTERVAL_SECONDS = 3 * 24 * 60 * 60


def _python_bin() -> str:
    return sys.executable


def _fetch_args(base_dir: Path) -> list[str]:
    return ["-m", "buonaiuto_doc4llm", "--base-dir", str(base_dir), "fetch"]


def _refresh_args(base_dir: Path) -> list[str]:
    return ["-m", "buonaiuto_doc4llm", "--base-dir", str(base_dir), "refresh-active"]


def _src_dir() -> str:
    return str(Path(__file__).resolve().parents[1])


# ------------------------------------------------------------------
# launchd (macOS)
# ------------------------------------------------------------------

def _plist_path(label: str) -> Path:
    return PLIST_DIR / f"{label}.plist"


def _build_fetch_plist(base_dir: Path, hour: int, minute: int) -> str:
    from html import escape as xml_escape
    python = _python_bin()
    src = _src_dir()
    args_xml = "\n".join(
        f"        <string>{xml_escape(str(a))}</string>"
        for a in [python] + _fetch_args(base_dir)
    )
    log_dir = base_dir / "state" / "logs"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL_FETCH}</string>

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


def _build_refresh_plist(base_dir: Path) -> str:
    from html import escape as xml_escape
    python = _python_bin()
    src = _src_dir()
    args_xml = "\n".join(
        f"        <string>{xml_escape(str(a))}</string>"
        for a in [python] + _refresh_args(base_dir)
    )
    log_dir = base_dir / "state" / "logs"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL_REFRESH}</string>

    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>{xml_escape(str(src))}</string>
    </dict>

    <key>StartInterval</key>
    <integer>{REFRESH_INTERVAL_SECONDS}</integer>

    <key>StandardOutPath</key>
    <string>{xml_escape(str(log_dir / "refresh-active.stdout.log"))}</string>
    <key>StandardErrorPath</key>
    <string>{xml_escape(str(log_dir / "refresh-active.stderr.log"))}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def install_schedule(base_dir: Path, hour: int = 4, minute: int = 0) -> dict[str, Any]:
    """Install both schedule entries (daily fetch + 3-day refresh)."""
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be 0-23, got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"minute must be 0-59, got {minute}")

    if platform.system() == "Darwin":
        return _install_launchd(base_dir, hour, minute)
    return _install_crontab(base_dir, hour, minute)


def uninstall_schedule() -> dict[str, Any]:
    """Remove both schedule entries."""
    if platform.system() == "Darwin":
        return _uninstall_launchd()
    return _uninstall_crontab()


def schedule_status() -> dict[str, Any]:
    """Check whether each schedule entry is installed."""
    if platform.system() == "Darwin":
        return _launchd_status()
    return _crontab_status()


def _install_launchd(base_dir: Path, hour: int, minute: int) -> dict[str, Any]:
    log_dir = base_dir / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    PLIST_DIR.mkdir(parents=True, exist_ok=True)

    fetch_plist = _plist_path(LAUNCHD_LABEL_FETCH)
    refresh_plist = _plist_path(LAUNCHD_LABEL_REFRESH)

    # Unload existing plists if present. Surface stderr so a malformed
    # plist doesn't silently linger.
    for p in (fetch_plist, refresh_plist):
        if p.exists():
            r = subprocess.run(["launchctl", "unload", str(p)],
                                capture_output=True, text=True)
            if r.returncode != 0 and r.stderr.strip():
                print(f"[scheduler] launchctl unload {p}: {r.stderr.strip()}",
                      file=sys.stderr)

    fetch_plist.write_text(_build_fetch_plist(base_dir, hour, minute), encoding="utf-8")
    refresh_plist.write_text(_build_refresh_plist(base_dir), encoding="utf-8")
    # Load both, rolling back fetch on refresh failure so we never leave
    # half a schedule behind.
    subprocess.run(["launchctl", "load", str(fetch_plist)], check=True, capture_output=True)
    try:
        subprocess.run(["launchctl", "load", str(refresh_plist)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(["launchctl", "unload", str(fetch_plist)], capture_output=True)
        raise

    refresh_minute = (minute + 15) % 60
    refresh_hour = (hour + (1 if (minute + 15) >= 60 else 0)) % 24
    return {
        "installed": True,
        "method": "launchd",
        # Back-compat summary string for callers that pre-date the two-entry
        # rewrite (e.g. the dashboard flash message).
        "schedule": (
            f"daily at {hour:02d}:{minute:02d} "
            f"+ refresh every 3 days at {refresh_hour:02d}:{refresh_minute:02d}"
        ),
        "entries": [
            {"label": LAUNCHD_LABEL_FETCH, "plist": str(fetch_plist),
             "schedule": f"daily at {hour:02d}:{minute:02d}"},
            {"label": LAUNCHD_LABEL_REFRESH, "plist": str(refresh_plist),
             "schedule": f"every 3 days (approx. {refresh_hour:02d}:{refresh_minute:02d})"},
        ],
        "log_dir": str(log_dir),
    }


def _uninstall_launchd() -> dict[str, Any]:
    removed: list[str] = []
    for label in (LAUNCHD_LABEL_FETCH, LAUNCHD_LABEL_REFRESH):
        plist = _plist_path(label)
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()
            removed.append(str(plist))
    if not removed:
        return {"uninstalled": False, "reason": "not_installed"}
    return {"uninstalled": True, "method": "launchd", "removed": removed}


def _launchd_status() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for label in (LAUNCHD_LABEL_FETCH, LAUNCHD_LABEL_REFRESH):
        plist = _plist_path(label)
        if not plist.exists():
            entries.append({"label": label, "installed": False})
            continue
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True,
        )
        entries.append({
            "label": label, "installed": True, "loaded": result.returncode == 0,
            "plist": str(plist),
        })
    any_installed = any(e["installed"] for e in entries)
    return {"installed": any_installed, "method": "launchd", "entries": entries}


# ------------------------------------------------------------------
# crontab (Linux)
# ------------------------------------------------------------------

_CRON_MARKER_FETCH = f"# {LAUNCHD_LABEL_FETCH}"
_CRON_MARKER_REFRESH = f"# {LAUNCHD_LABEL_REFRESH}"


def _cron_line_fetch(base_dir: Path, hour: int, minute: int) -> str:
    import shlex
    python = shlex.quote(str(_python_bin()))
    src = shlex.quote(str(_src_dir()))
    args = " ".join(shlex.quote(str(a)) for a in _fetch_args(base_dir))
    return f"{minute} {hour} * * * PYTHONPATH={src} {python} {args} {_CRON_MARKER_FETCH}"


def _cron_line_refresh(base_dir: Path, hour: int, minute: int) -> str:
    import shlex
    python = shlex.quote(str(_python_bin()))
    src = shlex.quote(str(_src_dir()))
    args = " ".join(shlex.quote(str(a)) for a in _refresh_args(base_dir))
    refresh_minute = (minute + 15) % 60
    refresh_hour = (hour + (1 if (minute + 15) >= 60 else 0)) % 24
    return (
        f"{refresh_minute} {refresh_hour} */3 * * "
        f"PYTHONPATH={src} {python} {args} {_CRON_MARKER_REFRESH}"
    )


def _install_crontab(base_dir: Path, hour: int, minute: int) -> dict[str, Any]:
    _remove_cron_entries()
    lines = [
        _cron_line_fetch(base_dir, hour, minute),
        _cron_line_refresh(base_dir, hour, minute),
    ]
    existing = _read_crontab()
    parts = [existing.rstrip("\n")] if existing.strip() else []
    parts.extend(lines)
    new_crontab = "\n".join(parts) + "\n"
    subprocess.run(
        ["crontab", "-"],
        input=new_crontab, text=True, check=True, capture_output=True,
    )
    refresh_minute = (minute + 15) % 60
    refresh_hour = (hour + (1 if (minute + 15) >= 60 else 0)) % 24
    return {
        "installed": True,
        "method": "crontab",
        # Back-compat summary string for the dashboard flash.
        "schedule": (
            f"daily at {hour:02d}:{minute:02d} "
            f"+ refresh every 3 days at {refresh_hour:02d}:{refresh_minute:02d}"
        ),
        "entries": lines,
    }


def _uninstall_crontab() -> dict[str, Any]:
    removed = _remove_cron_entries()
    if not removed:
        return {"uninstalled": False, "reason": "not_installed"}
    return {"uninstalled": True, "method": "crontab"}


def _crontab_status() -> dict[str, Any]:
    existing = _read_crontab()
    return {
        "installed": _CRON_MARKER_FETCH in existing or _CRON_MARKER_REFRESH in existing,
        "method": "crontab",
        "entries": [
            {"name": "daily-fetch", "installed": _CRON_MARKER_FETCH in existing},
            {"name": "refresh-active", "installed": _CRON_MARKER_REFRESH in existing},
        ],
    }


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        # `crontab -l` returns 1 both when the user has no crontab ("no
        # crontab for user") and when the `crontab` binary is missing or
        # unavailable. Surface real errors so uninstall etc. don't silently
        # no-op.
        stderr = (result.stderr or "").strip().lower()
        benign = ("no crontab" in stderr) or stderr == ""
        if not benign:
            print(f"[scheduler] crontab -l failed: {result.stderr.strip()}",
                  file=sys.stderr)
        return ""
    return result.stdout


def _remove_cron_entries() -> bool:
    existing = _read_crontab()
    if _CRON_MARKER_FETCH not in existing and _CRON_MARKER_REFRESH not in existing:
        return False
    kept = [
        l for l in existing.splitlines()
        if _CRON_MARKER_FETCH not in l and _CRON_MARKER_REFRESH not in l
    ]
    new_crontab = "\n".join(kept) + "\n" if kept else ""
    subprocess.run(
        ["crontab", "-"],
        input=new_crontab, text=True, check=True, capture_output=True,
    )
    return True


# Back-compat aliases for pre-existing callers (e.g. tests/test_scheduler.py)
_build_plist = _build_fetch_plist
_cron_line = _cron_line_fetch
_CRON_MARKER = _CRON_MARKER_FETCH
