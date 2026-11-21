"""Field management and the farm-area rule."""


async def test_field_crud(client, auth, create_farm, create_field):
    farm = await create_farm()
    field = await create_field(farm["id"])
    base = f"/farms/{farm['id']}/fields"

    assert [f["id"] for f in (await client.get(base, headers=auth())).json()] == [field["id"]]

    updated = await client.patch(
        f"{base}/{field['id']}", json={"irrigation_method": "sprinkler"}, headers=auth()
    )
    assert updated.status_code == 200
    assert updated.json()["irrigation_method"] == "sprinkler"
    assert updated.json()["area_ha"] == 4

    assert (await client.delete(f"{base}/{field['id']}", headers=auth())).status_code == 204
    assert (await client.get(f"{base}/{field['id']}", headers=auth())).status_code == 404


async def test_fields_cannot_exceed_farm_area(client, auth, create_farm, create_field):
    farm = await create_farm(total_area_ha=10)
    base = f"/farms/{farm['id']}/fields"
    first = await create_field(farm["id"], area_ha=6)

    too_big = await client.post(
        base, json={"name": "B", "area_ha": 4.5, "irrigation_method": "drip"}, headers=auth()
    )
    assert too_big.status_code == 422
    assert "4 ha available" in too_big.json()["detail"]

    await create_field(farm["id"], area_ha=4)  # exactly fills the farm

    # Growing an existing field is checked against the others only.
    grow = await client.patch(f"{base}/{first['id']}", json={"area_ha": 6.5}, headers=auth())
    assert grow.status_code == 422

    # The farm cannot shrink below its fields.
    shrink = await client.patch(
        f"/farms/{farm['id']}", json={"total_area_ha": 9}, headers=auth()
    )
    assert shrink.status_code == 422


async def test_no_area_limit_when_farm_area_unknown(create_farm, create_field):
    farm = await create_farm(total_area_ha=None)
    await create_field(farm["id"], area_ha=500)


async def test_field_from_another_farm_is_not_found(client, auth, create_farm, create_field):
    farm_a = await create_farm()
    farm_b = await create_farm()
    field_b = await create_field(farm_b["id"])

    response = await client.get(f"/farms/{farm_a['id']}/fields/{field_b['id']}", headers=auth())
    assert response.status_code == 404


async def test_field_validation(client, auth, create_farm):
    farm = await create_farm()
    base = f"/farms/{farm['id']}/fields"

    for body in (
        {"name": "A", "area_ha": 1, "irrigation_method": "hand"},
        {"name": "A", "area_ha": 1, "irrigation_method": "drip", "soil_texture": "rock"},
        {"name": "A", "area_ha": -1, "irrigation_method": "drip"},
        {"name": "A", "area_ha": 1},
    ):
        assert (await client.post(base, json=body, headers=auth())).status_code == 422
