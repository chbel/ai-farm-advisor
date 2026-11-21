"""Disease API endpoints."""

import pytest

from app.core import config
from app.main import app
from tests.conftest import AUTH, image_bytes, make_token


def upload(data: bytes, name: str = "leaf.jpg", mime: str = "image/jpeg"):
    return {"image": (name, data, mime)}


async def analyze(client, data=None, crop="tomato", headers=AUTH):
    return await client.post("/disease/analyze", files=upload(data or image_bytes()),
                             data={"crop_type": crop}, headers=headers)


async def test_requires_authentication(client):
    assert (await analyze(client, headers={})).status_code == 401
    expired = {"Authorization": f"Bearer {make_token(exp=0)}"}
    assert (await analyze(client, headers=expired)).status_code == 401


async def test_confident_disease_prediction(client):
    response = await analyze(client, image_bytes(color=(255, 0, 0)))

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "likely"
    assert body["top_prediction"]["label"] == "tomato_bacterial_spot"
    assert body["top_prediction"]["name_fr"] == "Tache bactérienne"
    assert body["top_prediction"]["probability"] > 0.9
    assert len(body["candidates"]) == 3
    assert sum(c["probability"] for c in body["candidates"]) <= 1.0001
    assert "not a diagnosis" in body["disclaimer"]
    assert "Confirm before treating" in body["message"]
    assert body["model"] == {"crop": "tomato", "version": "test-model-1",
                             "field_test_accuracy": 0.61, "lab_test_accuracy": 0.98}


async def test_healthy_prediction_has_its_own_message(client):
    body = (await analyze(client, image_bytes(color=(0, 255, 0)))).json()

    assert body["outcome"] == "likely"
    assert body["top_prediction"]["label"] == "tomato_healthy"
    assert body["top_prediction"]["cause_type"] == "none"
    assert "No disease symptoms recognized" in body["message"]


async def test_low_confidence_is_reported_as_uncertain(client):
    body = (await analyze(client, image_bytes(color=(128, 128, 128)))).json()

    assert body["outcome"] == "uncertain"
    assert "not confident" in body["message"]
    assert body["top_prediction"]["probability"] < config.settings.MIN_CONFIDENCE


async def test_olive_is_rejected_with_explanation(client):
    response = await analyze(client, crop="olive")

    assert response.status_code == 422
    assert "not available yet" in response.json()["detail"]


async def test_unknown_crop(client):
    assert (await analyze(client, crop="wheat")).status_code == 422


async def test_crop_without_installed_model_returns_503(client):
    app.state.models = {}
    assert (await analyze(client)).status_code == 503


@pytest.mark.parametrize(
    "data,name,mime",
    [(b"%PDF-1.4 fake", "leaf.jpg", "image/jpeg"),  # content is checked, not the file name
     (image_bytes(fmt="GIF"), "leaf.gif", "image/gif"),
     (b"", "leaf.jpg", "image/jpeg")],
)
async def test_invalid_uploads_return_415(client, data, name, mime):
    response = await client.post("/disease/analyze", files=upload(data, name, mime),
                                 data={"crop_type": "tomato"}, headers=AUTH)
    assert response.status_code == 415


async def test_oversized_upload_returns_413(client, monkeypatch):
    monkeypatch.setattr(config.settings, "MAX_UPLOAD_BYTES", 1000)
    assert (await analyze(client, image_bytes(size=(800, 800)))).status_code == 413


async def test_missing_file_returns_422(client):
    response = await client.post("/disease/analyze", data={"crop_type": "tomato"}, headers=AUTH)
    assert response.status_code == 422


async def test_models_endpoint_lists_classes_metrics_and_datasets(client):
    body = (await client.get("/disease/models", headers=AUTH)).json()

    assert body["supported_crops"] == ["olive", "tomato"]
    tomato = body["installed"][0]
    assert tomato["crop"] == "tomato" and len(tomato["classes"]) == 10
    assert tomato["classes"][2]["causal_agent"] == "Phytophthora infestans"
    assert tomato["datasets"][0]["license"] == "CC BY-SA 3.0"


async def test_readiness_depends_on_models(client):
    assert (await client.get("/health/ready")).json()["models"] == {"tomato": "test-model-1"}
    app.state.models = {}
    assert (await client.get("/health/ready")).status_code == 503
