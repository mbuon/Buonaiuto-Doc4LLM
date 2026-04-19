from __future__ import annotations

import json as _json
import sys
from datetime import datetime, timezone
from typing import Any


def mcp_args_summary(tool_name: str, args: dict[str, Any] | Any) -> str:
    if not isinstance(args, dict):
        return str(args) if args else "—"

    if tool_name in ("search_docs", "search_documentation"):
        tech = args.get("technology") or args.get("libraries") or ""
        if isinstance(tech, list):
            tech = ", ".join(str(t) for t in tech)
        q = args.get("query", "")
        if not tech and not q:
            return "—"
        return f'{tech}, "{q}"' if q else str(tech)
    if tool_name in ("read_doc", "read_full_page"):
        tech = args.get("technology", "")
        rel = args.get("rel_path", "")
        if not tech and not rel:
            return "—"
        return f"{tech}/{rel}"
    if tool_name in ("list_project_updates", "ack_project_updates"):
        return str(args.get("project_id") or "—")
    if tool_name == "fetch_docs":
        return args.get("technology") or "all"
    if tool_name == "install_project":
        return str(args.get("project_path") or "—")

    # Filter out None values and sort for deterministic display.
    items = [(k, v) for k, v in sorted(args.items()) if v is not None][:2]
    return ", ".join(f"{k}={v}" for k, v in items) or "—"


def humanize_timedelta(moment: datetime | str | None) -> str:
    if moment is None:
        return "never"
    original = moment
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            print(f"[dashboard] humanize_timedelta: could not parse {original!r}",
                  file=sys.stderr)
            return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    seconds = int(delta.total_seconds())
    if seconds < 0:
        # Clock skew — stamp from the future. Show "just now" rather than
        # a confusing negative interval.
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def truncate_chars(value: str, limit: int) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."


def fromjson(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return _json.loads(value)
    except (ValueError, TypeError) as exc:
        print(f"[dashboard] fromjson: could not parse column value: {exc}",
              file=sys.stderr)
        return {}
