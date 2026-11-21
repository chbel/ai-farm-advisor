"""Crops, growth stages and stage history."""

from datetime import date, timedelta

import pytest

TODAY = date.today()


def days(n: int) -> str:
    """ISO date `n` days from today (negative = past)."""
    return (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture
async def field_url(create_farm, create_field):
    farm = await create_farm()
    field = await create_field(farm["id"])
    return f"/farms/{farm['id']}/fields/{field['id']}/crops"


async def test_create_olive_crop_with_stage_starts_history(client, auth, field_url):
    response = await client.post(
        field_url,
        json={"crop_type": "olive", "variety": "Chemlali", "planting_date": "2015-03-01",
              "growth_stage": "flowering", "plant_count": 400},
        headers=auth(),
    )
    assert response.status_code == 201
    crop = response.json()
    assert crop["status"] == "active"
    assert crop["days_since_planting"] == (TODAY - date(2015, 3, 1)).days

    history = (await client.get(f"{field_url}/{crop['id']}/stages", headers=auth())).json()
    assert [(e["stage"], e["observed_on"]) for e in history] == [("flowering", TODAY.isoformat())]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"crop_type": "tomato", "planting_date": "2026-01-01",
                      "growth_stage": "pit_hardening"}, id="olive-stage-on-tomato"),
        pytest.param({"crop_type": "olive", "planting_date": "2026-01-01",
                      "growth_stage": "mid_season"}, id="tomato-stage-on-olive"),
        pytest.param({"crop_type": "tomato", "planting_date": days(5)}, id="future-date-active"),
        pytest.param({"crop_type": "tomato", "status": "planned", "growth_stage": "initial",
                      "planting_date": days(5)}, id="planned-with-stage"),
        pytest.param({"crop_type": "wheat", "planting_date": "2026-01-01"}, id="unknown-crop"),
        pytest.param({"crop_type": "tomato", "planting_date": "2026-01-01", "plant_count": 0},
                     id="zero-plants"),
    ],
)
async def test_crop_validation(client, auth, field_url, body):
    assert (await client.post(field_url, json=body, headers=auth())).status_code == 422


async def test_planned_crop_may_be_in_future(client, auth, field_url):
    response = await client.post(
        field_url,
        json={"crop_type": "tomato", "status": "planned", "planting_date": days(30)},
        headers=auth(),
    )
    assert response.status_code == 201
    assert response.json()["days_since_planting"] is None


async def test_update_crop_checks_resulting_combination(client, auth, field_url):
    crop = (await client.post(
        field_url,
        json={"crop_type": "tomato", "status": "planned", "planting_date": days(30)},
        headers=auth(),
    )).json()
    url = f"{field_url}/{crop['id']}"

    # Activating a crop whose planting date is still in the future is rejected.
    assert (await client.patch(url, json={"status": "active"}, headers=auth())).status_code == 422

    ok = await client.patch(
        url, json={"status": "active", "planting_date": days(0)}, headers=auth()
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "active"

    # Crop type and stage cannot be changed through PATCH.
    for body in ({"crop_type": "olive"}, {"growth_stage": "initial"}):
        assert (await client.patch(url, json=body, headers=auth())).status_code == 422


async def test_stage_history_and_current_stage(client, auth, field_url):
    crop = (await client.post(
        field_url, json={"crop_type": "tomato", "planting_date": days(-60)}, headers=auth()
    )).json()
    stages = f"{field_url}/{crop['id']}/stages"

    async def record(stage: str, offset: int):
        return await client.post(
            stages, json={"stage": stage, "observed_on": days(offset)}, headers=auth()
        )

    assert (await record("initial", -55)).status_code == 201
    assert (await record("mid_season", -5)).status_code == 201
    # A back-dated observation is added to history but does not become current.
    assert (await record("development", -30)).status_code == 201

    current = (await client.get(f"{field_url}/{crop['id']}", headers=auth())).json()
    assert current["growth_stage"] == "mid_season"
    history = (await client.get(stages, headers=auth())).json()
    assert [e["stage"] for e in history] == ["initial", "development", "mid_season"]

    assert (await record("pit_hardening", -1)).status_code == 422  # olive stage
    assert (await record("development", -61)).status_code == 422  # before planting
    assert (await record("development", 1)).status_code == 422  # future


async def test_crop_from_another_field_is_not_found(
    client, auth, create_farm, create_field, field_url
):
    farm = await create_farm()
    other_field = await create_field(farm["id"])
    crop = (await client.post(
        field_url, json={"crop_type": "olive", "planting_date": "2010-01-01"}, headers=auth()
    )).json()

    url = f"/farms/{farm['id']}/fields/{other_field['id']}/crops/{crop['id']}"
    assert (await client.get(url, headers=auth())).status_code == 404
