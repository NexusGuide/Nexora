"""Describe a live panel's API shape without revealing anything in it.

    docker compose exec api python -m app.panels.probe [username]

Panels differ in how a user is given access to inbounds: Marzban-style panels
attach `proxies`/`inbounds` to the user directly, while newer PasarGuard
versions assign inbounds to *groups* and put users in groups. An adapter that
guesses wrong creates users who exist but have no links. This asks the panel
which model it uses.

It prints structure only — field names, types, counts, status codes — never
values. It authenticates with the credentials already stored (encrypted) in
the database, so nothing sensitive has to be typed or pasted anywhere.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app.db.base import SessionFactory
from app.models.panel import Panel
from app.panels.manager import PanelManager


def _shape(value: Any, depth: int = 0) -> Any:
    """Replace every value with its type, keeping the structure."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"dict[{len(value)} keys]"
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return "list[empty]"
        return [f"list[{len(value)}] of", _shape(value[0], depth + 1)]
    if value is None:
        return "null"
    return type(value).__name__


async def _get(adapter: Any, path: str) -> tuple[int, Any]:
    response = await adapter._request("GET", path)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


async def main(username: str | None) -> None:
    async with SessionFactory() as session:
        panel = await session.scalar(select(Panel).order_by(Panel.created_at).limit(1))
        if panel is None:
            print("No panel registered.")
            return

        report: dict[str, Any] = {"panel_type": panel.panel_type.value}
        adapter = PanelManager().adapter_for(panel)
        async with adapter:
            # Group model (newer PasarGuard).
            code, body = await _get(adapter, "/api/groups")
            groups = body.get("groups", body) if isinstance(body, dict) else body
            report["GET /api/groups"] = {
                "status": code,
                "count": len(groups) if isinstance(groups, list) else None,
                "item_shape": (
                    _shape(groups[0]) if isinstance(groups, list) and groups else None
                ),
                # Group names and their inbound counts are operational, not
                # secret, and are needed to pick one.
                "groups": [
                    {
                        "id": g.get("id"),
                        "name": g.get("name"),
                        "inbound_tags": len(g.get("inbound_tags") or []),
                        "is_disabled": g.get("is_disabled"),
                    }
                    for g in groups
                ]
                if isinstance(groups, list)
                else None,
            }

            # Inbound tags (both models). Tags are names, not credentials.
            code, body = await _get(adapter, "/api/inbounds")
            report["GET /api/inbounds"] = {"status": code, "shape": _shape(body)}

            # One user, to see which access fields exist.
            if username:
                code, body = await _get(adapter, f"/api/user/{username}")
                shape = _shape(body)
                report["GET /api/user/{username}"] = {
                    "status": code,
                    "fields": sorted(body) if isinstance(body, dict) else None,
                    "shape": shape,
                    "links_count": len(body.get("links") or [])
                    if isinstance(body, dict)
                    else None,
                }

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
