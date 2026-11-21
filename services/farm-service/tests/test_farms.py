"""Farm CRUD, ownership isolation and authentication."""

import pytest

from tests.conftest import FARM, make_token


async def test_requires_authentication(client):
    assert (await client.get("/farms")).status_code == 401
    bad = {"Authorization": f"Bearer {make_token(aud='other-app')}"}
    assert (await client.get("/farms", headers=bad)).status_code == 401


async def test_create_farm_sets_owner_from_token(client, auth):
    response = await client.post(
        "/farms", json={**FARM, "owner_id": 999}, headers=auth(user_id=7)
    )
    assert response.status_code == 422  # owner_id is not accepted from clients

    response = await client.post("/farms", json=FARM, headers=auth(user_id=7))
    assert response.status_code == 201
    assert response.json()["owner_id"] == 7
    assert response.json()["governorate"] == "sfax"


@pytest.mark.parametrize(
    "override",
    [{"latitude": 91}, {"longitude": -181}, {"total_area_ha": 0},
     {"governorate": "paris"}, {"name": ""}],
)
async def test_create_farm_validation(client, auth, override):
    response = await client.post("/farms", json={**FARM, **override}, headers=auth())
    assert response.status_code == 422


async def test_users_only_see_their_own_farms(client, auth, create_farm):
    mine = await create_farm(user_id=1)
    theirs = await create_farm(user_id=2, name="Other farm")

    listing = (await client.get("/farms", headers=auth(1))).json()
    assert [f["id"] for f in listing["items"]] == [mine["id"]]
    assert listing["total"] == 1

    # Another user's farm is reported as missing, for every operation.
    url = f"/farms/{theirs['id']}"
    assert (await client.get(url, headers=auth(1))).status_code == 404
    assert (await client.patch(url, json={"name": "x"}, headers=auth(1))).status_code == 404
    assert (await client.delete(url, headers=auth(1))).status_code == 404
    assert (await client.get(f"{url}/fields", headers=auth(1))).status_code == 404


async def test_admin_sees_all_farms_and_can_filter_by_owner(client, auth, create_farm):
    await create_farm(user_id=1)
    await create_farm(user_id=2)

    everything = (await client.get("/farms", headers=auth(99, "admin"))).json()
    assert everything["total"] == 2
    only_two = (await client.get("/farms?owner_id=2", headers=auth(99, "admin"))).json()
    assert [f["owner_id"] for f in only_two["items"]] == [2]

    # owner_id filter is ignored for non-admins.
    farmer = (await client.get("/farms?owner_id=2", headers=auth(1))).json()
    assert [f["owner_id"] for f in farmer["items"]] == [1]


async def test_update_farm_partial_and_rejects_null_required(client, auth, create_farm):
    farm = await create_farm()
    url = f"/farms/{farm['id']}"

    response = await client.patch(
        url, json={"name": "Nouveau nom", "governorate": None}, headers=auth()
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Nouveau nom"
    assert response.json()["governorate"] is None
    assert response.json()["latitude"] == FARM["latitude"]

    assert (await client.patch(url, json={"name": None}, headers=auth())).status_code == 422
    assert (await client.patch(url, json={"unknown": 1}, headers=auth())).status_code == 422


async def test_delete_farm_cascades(client, auth, create_farm, create_field):
    farm = await create_farm()
    await create_field(farm["id"])

    assert (await client.delete(f"/farms/{farm['id']}", headers=auth())).status_code == 204
    assert (await client.get(f"/farms/{farm['id']}", headers=auth())).status_code == 404


async def test_catalog_lists_stages_per_crop(client, auth):
    catalog = (await client.get("/farms/catalog", headers=auth())).json()

    assert len(catalog["governorates"]) == 24
    assert catalog["crops"]["tomato"]["growth_stages"] == [
        "initial", "development", "mid_season", "late_season"
    ]
    assert "pit_hardening" in catalog["crops"]["olive"]["growth_stages"]


async def test_pagination(client, auth, create_farm):
    for i in range(3):
        await create_farm(name=f"Farm {i}")

    page = (await client.get("/farms?limit=2&offset=2", headers=auth())).json()
    assert page["total"] == 3
    assert [f["name"] for f in page["items"]] == ["Farm 0"]  # newest first
