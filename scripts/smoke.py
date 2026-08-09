"""Exercise the tools against a live Mealie instance.

Not part of the test suite — it writes real data. Point it at a scratch
instance:

    MEALIE_URL=http://localhost:9000 MEALIE_API_TOKEN=... \
        python scripts/smoke.py [--write]

Without --write it only runs read tools.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from typing import Any

from fastmcp import Client

from mealie_mcp.config import Config
from mealie_mcp.server import build_server


def data(result) -> Any:
    """Decode a tool result.

    The server sends results as JSON text only — the duplicate structured copy
    is stripped by SendResultsOnce — so fastmcp's `.data` shortcut is None.
    """
    return json.loads(result.content[0].text)


def show(label: str, result) -> None:
    text = json.dumps(data(result), indent=2, default=str)
    print(f"\n--- {label}\n{text[:1200]}")


async def main(write: bool) -> None:
    config = Config.from_env()
    async with Client(build_server(config)) as client:
        tools = sorted(t.name for t in await client.list_tools())
        print(f"{len(tools)} tools: {', '.join(tools)}")

        show("search_recipes", await client.call_tool("search_recipes", {"limit": 3}))
        show(
            "manage_taxonomy(tags)",
            await client.call_tool("manage_taxonomy", {"resource": "tags", "action": "list"}),
        )
        show("get_todays_meals", await client.call_tool("get_todays_meals", {}))
        show("list_cookbooks", await client.call_tool("list_cookbooks", {}))

        if not write:
            print("\nread-only pass complete (use --write to exercise writes)")
            return

        created = await client.call_tool(
            "create_recipe",
            {
                "name": "Smoke Test Pancakes",
                "description": "Created by scripts/smoke.py",
                "ingredients": ["2 cups flour", "1 tbsp sugar", "a pinch of salt"],
                "instructions": ["Mix everything.", "Fry until golden."],
                "tags": ["Smoke Test", "Breakfast"],
                "recipe_yield": "4 servings",
            },
        )
        show("create_recipe", created)
        slug = data(created)["slug"]

        show(
            "update_recipe (tags should merge)",
            await client.call_tool("update_recipe", {"slug": slug, "tags": ["Quick"]}),
        )

        today = date.today()  # noqa: DTZ011
        show(
            "add_meal_plan_entry",
            await client.call_tool(
                "add_meal_plan_entry",
                {"date": today.isoformat(), "recipe_slug": slug, "entry_type": "breakfast"},
            ),
        )
        show(
            "get_meal_plan",
            await client.call_tool(
                "get_meal_plan",
                {
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=1)).isoformat(),
                },
            ),
        )

        book = await client.call_tool(
            "create_cookbook",
            {"name": "Smoke Test Book", "query_filter": 'tags.name IN ["Smoke Test"]'},
        )
        show("create_cookbook", book)
        show(
            "get_cookbook_recipes",
            await client.call_tool("get_cookbook_recipes", {"cookbook": data(book)["cookbook_id"]}),
        )

        show(
            "delete_cookbook",
            await client.call_tool("delete_cookbook", {"cookbook_id": data(book)["cookbook_id"]}),
        )
        show(
            "delete_recipe",
            await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug}),
        )


if __name__ == "__main__":
    asyncio.run(main("--write" in sys.argv))
