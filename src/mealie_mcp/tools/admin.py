"""Ingredient parsing and taxonomy management.

Taxonomy is the one grouped tool: five resources times three actions is
fifteen tools nobody reaches for often enough to deserve the room.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MERGE_KEYS, TAXONOMY_PATHS, MealieClient

GetClient = Callable[[], MealieClient]

RESOURCES = tuple(TAXONOMY_PATHS)
ACTIONS = ("list", "create", "update", "merge", "delete")
PAGE_SIZE = 200


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def parse_ingredients(lines: list[str]) -> dict:
        """Parse free-text ingredient lines into quantity, unit, and food.

        Useful for inspecting how Mealie will read a list. create_recipe
        accepts plain text directly, so this is not required before writing.
        """
        results = await get_client().request(
            "POST", "/api/parser/ingredients", json={"ingredients": lines}
        )
        return {"items": [shape.parsed_ingredient(r) for r in results or []]}

    allowed_actions = ("list",) if read_only else ACTIONS

    @mcp.tool
    async def manage_taxonomy(
        resource: str,
        action: str = "list",
        name: str | None = None,
        item_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        data: dict[str, Any] | None = None,
        merge_into: str | None = None,
    ) -> dict:
        """List, create, update, merge, or delete Mealie's organizing entities.

        resource: foods, units, labels, tags, categories, or tools
        action:
          list (default) — 200 per page; the reply carries total and, when
            there is more, the page to ask for next
          create — needs name; data may carry extra fields
          update — needs item_id; renames with name, and/or sets data
          merge  — foods and units only: folds item_id into merge_into and
            repoints every recipe that used it
          delete — needs item_id

        data holds the fields Mealie stores beyond the name. For foods:
        description, pluralName, labelId (from resource="labels"), and
        aliases as [{"name": "..."}]. For units: abbreviation, pluralName,
        useAbbreviation. Unmentioned fields keep their current value.

        Recipe tools accept tag and category names directly and create them as
        needed, so this is mostly for tidying up.
        """
        if resource not in RESOURCES:
            raise ToolError(f"resource must be one of {', '.join(RESOURCES)} (got {resource!r})")
        if action not in allowed_actions:
            if action in ACTIONS:
                raise ToolError("server is in read-only mode")
            raise ToolError(f"action must be one of {', '.join(allowed_actions)}")

        client = get_client()
        path = TAXONOMY_PATHS[resource]

        if action == "list":
            result = await client.request(
                "GET", path, params={"search": search, "page": page, "perPage": PAGE_SIZE}
            )
            return shape.paginated(result, shape.taxonomy_item, page_number=page)

        if action == "create":
            if not name:
                raise ToolError("create requires a name")
            created = await client.request("POST", path, json={**(data or {}), "name": name})
            client.forget_taxonomy(resource)
            return shape.taxonomy_item(created)

        if action == "merge":
            if resource not in MERGE_KEYS:
                raise ToolError(
                    f"merge is only supported for {', '.join(MERGE_KEYS)} — "
                    f"for {resource}, re-tag the recipes and delete the leftover"
                )
            if not item_id or not merge_into:
                raise ToolError("merge requires item_id (the loser) and merge_into (the keeper)")
            from_key, to_key = MERGE_KEYS[resource]
            merged = await client.request(
                "PUT", f"{path}/merge", json={from_key: item_id, to_key: merge_into}
            )
            client.forget_taxonomy(resource)
            return {**shape.taxonomy_item(merged or {}), "merged": item_id, "into": merge_into}

        if action == "update":
            if not item_id:
                raise ToolError("update requires an item_id (get it from action='list')")
            if name is None and not data:
                raise ToolError("update requires a name and/or data")
            missing = f"{resource} item {item_id!r} not found"
            # Mealie's PUT replaces the whole row, so patch onto the current one.
            current = await client.request("GET", f"{path}/{item_id}", not_found=missing)
            payload = {**(current or {}), **(data or {})}
            if name is not None:
                payload["name"] = name
            updated = await client.request(
                "PUT", f"{path}/{item_id}", json=payload, not_found=missing
            )
            client.forget_taxonomy(resource)
            return shape.taxonomy_item(updated or payload)

        if not item_id:
            raise ToolError("delete requires an item_id (get it from action='list')")
        await client.request(
            "DELETE", f"{path}/{item_id}", not_found=f"{resource} item {item_id!r} not found"
        )
        client.forget_taxonomy(resource)
        return {"deleted": item_id}
