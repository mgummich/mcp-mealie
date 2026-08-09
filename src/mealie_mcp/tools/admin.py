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
#: Rows per list page. A food carries description, plural name, label, and
#: aliases, so 200 of them is a 12k-token reply for a question that is almost
#: always answered by the first few rows or by search.
PAGE_SIZE = 50
#: Per-item keys accepted inside the batch `items` list.
ITEM_KEYS = ("name", "item_id", "data", "merge_into")

#: What manage_taxonomy says when the server is read-only. The full docstring
#: documents create, update, merge, and delete, all of which are refused in
#: that mode, so it is replaced rather than shown and then contradicted.
READ_ONLY_DOC = """List Mealie's organizing entities.

resource: foods, units, labels, tags, categories, or tools
action: list only — this server is running read-only, so create, update,
  merge, and delete are refused. Listing returns 50 per page, with the
  total and the page to ask for next.
search narrows the list; page walks it.
"""


async def _apply(
    client: MealieClient,
    resource: str,
    action: str,
    *,
    name: str | None = None,
    item_id: str | None = None,
    data: dict[str, Any] | None = None,
    merge_into: str | None = None,
) -> dict:
    """Run one create/update/merge/delete against a taxonomy resource.

    Args:
        client: The live Mealie client.
        resource: Key into TAXONOMY_PATHS.
        action: One of create, update, merge, delete.
        name: New name, for create and update.
        item_id: Target item, for update, merge, and delete.
        data: Extra fields to set, for create and update.
        merge_into: The keeper, for merge.

    Returns:
        The shaped item, or a {"deleted": id} receipt.

    Raises:
        ToolError: On a missing argument or an unsupported merge.
    """
    path = TAXONOMY_PATHS[resource]

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
        updated = await client.request("PUT", f"{path}/{item_id}", json=payload, not_found=missing)
        client.forget_taxonomy(resource)
        return shape.taxonomy_item(updated or payload)

    if not item_id:
        raise ToolError("delete requires an item_id (get it from action='list')")
    await client.request(
        "DELETE", f"{path}/{item_id}", not_found=f"{resource} item {item_id!r} not found"
    )
    client.forget_taxonomy(resource)
    return {"deleted": item_id}


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

    # In read-only mode every write action is refused, so describing them
    # only buys a round trip that ends in "server is in read-only mode".
    @mcp.tool(description=READ_ONLY_DOC if read_only else None)
    async def manage_taxonomy(
        resource: str,
        action: str = "list",
        name: str | None = None,
        item_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        data: dict[str, Any] | None = None,
        merge_into: str | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict:
        """List, create, update, merge, or delete Mealie's organizing entities.

        resource: foods, units, labels, tags, categories, or tools
        action:
          list (default) — 50 per page; the reply carries total and, when
            there is more, the page to ask for next
          create — needs name; data may carry extra fields
          update — needs item_id; renames with name, and/or sets data
          merge  — foods and units only: folds item_id into merge_into and
            repoints every recipe that used it
          delete — needs item_id

        items batches any action except list: pass a list of per-item dicts
        using the same keys as the single form — name, item_id, data,
        merge_into — and every one runs in this single call. Twenty-five
        renames is one call, not twenty-five. The reply carries results and
        errors separately; one bad item does not stop the rest. Example:
        action="update", items=[{"item_id": "...", "name": "Scallion"},
        {"item_id": "...", "data": {"pluralName": "Onions"}}].

        data holds the fields Mealie stores beyond the name:
          foods — description, pluralName, labelId (from resource="labels"),
            aliases as [{"name": "..."}], extras
          units — description, pluralName, abbreviation, pluralAbbreviation,
            useAbbreviation, fraction, aliases as [{"name": "..."}], extras
          labels — color as a hex string, e.g. "#adb5bd"
          tools — householdsWithTool as a list of household slugs
          tags, categories — name only
        Unmentioned fields keep their current value, but a list field is
        replaced wholesale: send every alias you want kept, not the new one.

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

        if action == "list":
            if items:
                raise ToolError("items batches writes, not list")
            result = await client.request(
                "GET",
                TAXONOMY_PATHS[resource],
                params={"search": search, "page": page, "perPage": PAGE_SIZE},
            )
            return shape.paginated(result, shape.taxonomy_item, page_number=page)

        if items is None:
            return await _apply(
                client,
                resource,
                action,
                name=name,
                item_id=item_id,
                data=data,
                merge_into=merge_into,
            )

        if not items:
            raise ToolError("items is empty — nothing to do")

        results: list[dict] = []
        errors: list[dict] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append({"index": index, "item": item, "error": "not an object"})
                continue
            unknown = [k for k in item if k not in ITEM_KEYS]
            if unknown:
                errors.append(
                    {
                        "index": index,
                        "item": item,
                        "error": f"unknown keys {unknown} — use {', '.join(ITEM_KEYS)}",
                    }
                )
                continue
            if item.get("data") is not None and not isinstance(item["data"], dict):
                errors.append({"index": index, "item": item, "error": "data must be an object"})
                continue
            try:
                results.append(await _apply(client, resource, action, **item))
            except (ToolError, TypeError, ValueError) as exc:
                # One bad item should not strand the other twenty-four writes,
                # and a malformed value raises TypeError, not ToolError.
                errors.append({"index": index, "item": item, "error": str(exc)})

        return {
            "action": action,
            "resource": resource,
            "results": results,
            "count": len(results),
            "errors": errors,
            "failed": len(errors),
        }
