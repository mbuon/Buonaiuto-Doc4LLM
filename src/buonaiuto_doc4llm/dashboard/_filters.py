from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any


def mcp_args_summary(tool_name: str, args: dict[str, Any] | Any) -> str:
    if not isinstance(args, dict):
        return str(args)

    if tool_name in ("search_docs", "search_documentation"):
        tech = args.get("technology") or args.get("libraries") or ""
        if isinstance(tech, list):
            tech = ", ".join(str(t) for t in tech)
        q = args.get("query", "")
        return f'{tech}, "{q}"'
    if tool_name in ("read_doc", "read_full_page"):
        return f'{args.get("technology", "")}/{args.get("rel_path", "")}'
    if tool_name in ("list_project_updates", "ack_project_updates"):
        return str(args.get("project_id", ""))
    if tool_name == "fetch_docs":
        return args.get("technology") or "all"
    if tool_name == "install_project":
        return str(args.get("project_path", ""))

    items = list(args.items())[:2]
    return ", ".join(f"{k}={v}" for k, v in items) or "—"


def humanize_timedelta(moment: datetime | str | None) -> str:
    if moment is None:
        return "never"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    seconds = int(delta.total_seconds())
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
    return s if len(s) <= limit else s[:limit] + "…"


def fromjson(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return _json.loads(value)
    except (ValueError, TypeError):
        return {}
