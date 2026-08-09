"""Client tests: auth, error mapping, retry policy, and the caches."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from mealie_mcp.client import MealieClient, slugify

BASE = "https://mealie.test"


@pytest.fixture
async def client():
    c = MealieClient(BASE, "secret-token")
    yield c
    await c.aclose()


@respx.mock
async def test_sends_the_bearer_token(client):
    route = respx.get(f"{BASE}/api/recipes").mock(return_value=httpx.Response(200, json={}))

    await client.request("GET", "/api/recipes")

    assert route.calls.last.request.headers["Authorization"] == "Bearer secret-token"


@respx.mock
async def test_drops_none_params_so_they_are_not_sent_as_the_string_none(client):
    route = respx.get(f"{BASE}/api/recipes").mock(return_value=httpx.Response(200, json={}))

    await client.request("GET", "/api/recipes", params={"search": None, "page": 1})

    assert "search" not in route.calls.last.request.url.params
    assert route.calls.last.request.url.params["page"] == "1"


@respx.mock
async def test_401_names_the_token_variable(client):
    respx.get(f"{BASE}/api/recipes").mock(return_value=httpx.Response(401))

    with pytest.raises(ToolError, match="MEALIE_API_TOKEN"):
        await client.request("GET", "/api/recipes")


@respx.mock
async def test_404_uses_the_caller_supplied_message(client):
    respx.get(f"{BASE}/api/recipes/nope").mock(return_value=httpx.Response(404))

    with pytest.raises(ToolError, match="recipe 'nope' not found"):
        await client.request("GET", "/api/recipes/nope", not_found="recipe 'nope' not found")


@respx.mock
async def test_422_is_trimmed_to_the_failing_fields(client):
    respx.post(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            422,
            json={
                "detail": [
                    {"loc": ["body", "name"], "msg": "field required", "type": "missing"},
                    {"loc": ["body", "slug"], "msg": "too short", "type": "value_error"},
                ]
            },
        )
    )

    with pytest.raises(ToolError) as caught:
        await client.request("POST", "/api/recipes", json={})

    message = str(caught.value)
    assert "name: field required" in message
    assert "slug: too short" in message
    assert "traceback" not in message.lower()


@respx.mock
async def test_transport_failure_names_the_instance(client):
    respx.get(f"{BASE}/api/recipes").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(ToolError, match=f"Mealie unreachable at {BASE}"):
        await client.request("GET", "/api/recipes")


@respx.mock
async def test_get_retries_then_succeeds(client):
    route = respx.get(f"{BASE}/api/recipes").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"items": []})]
    )

    assert await client.request("GET", "/api/recipes") == {"items": []}
    assert route.call_count == 2


@respx.mock
async def test_get_retries_a_500(client):
    route = respx.get(f"{BASE}/api/recipes").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"ok": True})]
    )

    assert await client.request("GET", "/api/recipes") == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_writes_are_never_retried(client):
    # No idempotency key exists, so a retried create would duplicate the recipe.
    route = respx.post(f"{BASE}/api/recipes").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(ToolError):
        await client.request("POST", "/api/recipes", json={"name": "x"})

    assert route.call_count == 1


@respx.mock
async def test_bare_slug_response_is_returned_as_a_string(client):
    respx.post(f"{BASE}/api/recipes").mock(return_value=httpx.Response(201, text='"roast-chicken"'))

    assert await client.request("POST", "/api/recipes", json={"name": "x"}) == "roast-chicken"


@respx.mock
async def test_204_returns_none(client):
    respx.delete(f"{BASE}/api/recipes/x").mock(return_value=httpx.Response(204))

    assert await client.request("DELETE", "/api/recipes/x") is None


@respx.mock
async def test_recipe_id_is_fetched_once_then_cached(client):
    route = respx.get(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(200, json={"id": "uuid-1", "slug": "roast"})
    )

    assert await client.recipe_id("roast") == "uuid-1"
    assert await client.recipe_id("roast") == "uuid-1"
    assert route.call_count == 1


@respx.mock
async def test_taxonomy_resolves_existing_names_case_insensitively(client):
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "t1", "name": "Vegan", "slug": "vegan"}]}
        )
    )

    resolved, created = await client.resolve_taxonomy("tags", ["vegan"])

    assert resolved == [{"id": "t1", "name": "Vegan", "slug": "vegan"}]
    assert created == []


@respx.mock
async def test_taxonomy_creates_missing_names_and_reports_them(client):
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    create = respx.post(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(201, json={"id": "t2", "name": "Quick", "slug": "quick"})
    )

    resolved, created = await client.resolve_taxonomy("tags", ["Quick"])

    assert created == ["Quick"]
    assert resolved[0]["slug"] == "quick"
    assert create.call_count == 1

    # Second call is served from cache, not created again.
    _, created_again = await client.resolve_taxonomy("tags", ["Quick"])
    assert created_again == []
    assert create.call_count == 1


@respx.mock
async def test_taxonomy_refuses_to_create_when_told_not_to(client):
    respx.get(f"{BASE}/api/foods").mock(return_value=httpx.Response(200, json={"items": []}))

    with pytest.raises(ToolError, match="food 'Truffle' does not exist"):
        await client.resolve_taxonomy("foods", ["Truffle"], create_missing=False)


@respx.mock
async def test_unknown_filter_names_slugify_instead_of_raising(client):
    # Filtering by a tag that does not exist should return nothing, not error.
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    assert await client.taxonomy_slugs("tags", ["Slow Cooker"]) == ["slow-cooker"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Slow Cooker", "slow-cooker"), ("30-Minute!", "30-minute"), ("Café", "caf")],
)
def test_slugify(name, expected):
    assert slugify(name) == expected
