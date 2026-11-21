"""Model loading and calibrated inference."""

import json

import numpy as np
import pytest

from app.services.model import ModelLoadError, load_crop_model, load_models
from tests.model_factory import LABELS, write_model_folder


def red_batch() -> np.ndarray:
    batch = np.zeros((1, 3, 224, 224), dtype=np.float32)
    batch[0, 0] = 2.0  # strong red channel after normalization
    return batch


def test_loads_and_predicts(tmp_path):
    model = load_crop_model(write_model_folder(tmp_path / "tomato"), threads=1)

    probs = model.probabilities(red_batch())[0]
    assert model.crop == "tomato" and model.version == "test-model-1"
    assert probs.sum() == pytest.approx(1.0, abs=1e-6)
    assert probs.argmax() == 0


def test_temperature_softens_probabilities(tmp_path):
    sharp = load_crop_model(write_model_folder(tmp_path / "a", temperature=1.0), threads=1)
    soft = load_crop_model(write_model_folder(tmp_path / "b", temperature=3.0), threads=1)

    assert soft.probabilities(red_batch())[0].max() < sharp.probabilities(red_batch())[0].max()


def test_rejects_label_count_mismatch(tmp_path):
    with pytest.raises(ModelLoadError, match="outputs"):
        load_crop_model(write_model_folder(tmp_path / "m", n_outputs=5), threads=1)


def test_rejects_labels_missing_from_catalog(tmp_path):
    with pytest.raises(ModelLoadError, match="catalog"):
        load_crop_model(write_model_folder(tmp_path / "m", labels=LABELS[:-1] + ["tomato_unknown"]), threads=1)


def test_rejects_bad_temperature_and_metadata(tmp_path):
    with pytest.raises(ModelLoadError, match="temperature"):
        load_crop_model(write_model_folder(tmp_path / "t", temperature=0), threads=1)

    folder = write_model_folder(tmp_path / "m")
    meta = json.loads((folder / "metadata.json").read_text())
    del meta["preprocessing"]["mean"]
    (folder / "metadata.json").write_text(json.dumps(meta))
    with pytest.raises(ModelLoadError, match="metadata"):
        load_crop_model(folder, threads=1)


def test_rejects_corrupt_model_file(tmp_path):
    folder = write_model_folder(tmp_path / "m")
    (folder / "model.onnx").write_bytes(b"garbage")
    with pytest.raises(ModelLoadError, match="cannot load"):
        load_crop_model(folder, threads=1)


def test_load_models_skips_broken_folders(tmp_path):
    write_model_folder(tmp_path / "tomato")
    (tmp_path / "empty").mkdir()

    models = load_models(tmp_path, threads=1)
    assert list(models) == ["tomato"]
    assert load_models(tmp_path / "missing", threads=1) == {}
