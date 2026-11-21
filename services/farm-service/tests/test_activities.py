"""Farm journal."""

from datetime import date, timedelta

TODAY = date.today()


def days(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


async def _setup(client, auth, create_farm, create_field):
    farm = await create_farm()
    field = await create_field(farm["id"])
    crop = (await client.post(
        f"/farms/{farm['id']}/fields/{field['id']}/crops",
        json={"crop_type": "tomato", "planting_date": days(-20)},
        headers=auth(),
    )).json()
    return farm, field, crop


async def test_record_and_filter_activities(client, auth, create_farm, create_field):
    farm, field, crop = await _setup(client, auth, create_farm, create_field)
    base = f"/farms/{farm['id']}/activities"

    irrigation = await client.post(base, json={
        "activity_type": "irrigation", "performed_on": days(-2),
        "crop_id": crop["id"], "quantity": 25, "unit": "m3",
    }, headers=auth())
    assert irrigation.status_code == 201
    assert irrigation.json()["field_id"] == field["id"]  # inferred from the crop
    assert irrigation.json()["recorded_by"] == 1

    await client.post(base, json={
        "activity_type": "fertilization", "performed_on": days(0),
        "field_id": field["id"], "quantity": 50, "unit": "kg", "product": "NPK 15-15-15",
    }, headers=auth())

    everything = (await client.get(base, headers=auth())).json()
    assert everything["total"] == 2
    assert everything["items"][0]["activity_type"] == "fertilization"  # most recent first

    only = (await client.get(f"{base}?activity_type=irrigation", headers=auth())).json()
    assert [a["activity_type"] for a in only["items"]] == ["irrigation"]

    window = (await client.get(f"{base}?date_to={days(-1)}", headers=auth())).json()
    assert window["total"] == 1


async def test_activity_references_must_belong_to_farm(client, auth, create_farm, create_field):
    farm, field, crop = await _setup(client, auth, create_farm, create_field)
    other_farm = await create_farm()
    other_field = await create_field(other_farm["id"])
    base = f"/farms/{farm['id']}/activities"
    day = days(0)

    for body in (
        {"activity_type": "pruning", "performed_on": day, "field_id": other_field["id"]},
        {"activity_type": "pruning", "performed_on": day, "crop_id": 9999},
        {"activity_type": "pruning", "performed_on": day, "crop_id": crop["id"],
         "field_id": other_field["id"]},
    ):
        assert (await client.post(base, json=body, headers=auth())).status_code == 422


async def test_activity_validation(client, auth, create_farm):
    farm = await create_farm()
    base = f"/farms/{farm['id']}/activities"

    future = {"activity_type": "harvest", "performed_on": days(1)}
    no_unit = {"activity_type": "harvest", "performed_on": days(0), "quantity": 3}
    negative = {"activity_type": "harvest", "performed_on": days(0), "quantity": -1, "unit": "kg"}
    for body in (future, no_unit, negative):
        assert (await client.post(base, json=body, headers=auth())).status_code == 422


async def test_journal_survives_field_deletion(client, auth, create_farm, create_field):
    farm, field, crop = await _setup(client, auth, create_farm, create_field)
    base = f"/farms/{farm['id']}/activities"
    await client.post(
        base,
        json={"activity_type": "harvest", "performed_on": days(0), "crop_id": crop["id"]},
        headers=auth(),
    )

    await client.delete(f"/farms/{farm['id']}/fields/{field['id']}", headers=auth())

    entry = (await client.get(base, headers=auth())).json()["items"][0]
    assert (entry["field_id"], entry["crop_id"]) == (None, None)


async def test_delete_activity(client, auth, create_farm):
    farm = await create_farm()
    base = f"/farms/{farm['id']}/activities"
    entry = (await client.post(
        base, json={"activity_type": "observation", "performed_on": days(0)}, headers=auth()
    )).json()

    assert (await client.delete(f"{base}/{entry['id']}", headers=auth())).status_code == 204
    assert (await client.delete(f"{base}/{entry['id']}", headers=auth())).status_code == 404
