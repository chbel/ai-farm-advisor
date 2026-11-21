"""Loads crop models (ONNX + metadata) and runs calibrated inference."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.services.catalog import CATALOG

logger = logging.getLogger("disease_service.model")


class ModelLoadError(RuntimeError):
    pass


@dataclass
class CropModel:
    crop: str
    version: str
    labels: list[str]
    preprocessing: dict
    temperature: float
    metadata: dict
    session: ort.InferenceSession

    def probabilities(self, batch: np.ndarray) -> np.ndarray:
        """Calibrated class probabilities: softmax(logits / T) (temperature scaling)."""
        logits = self.session.run(None, {self.session.get_inputs()[0].name: batch})[0]
        scaled = logits / self.temperature
        scaled = scaled - scaled.max(axis=1, keepdims=True)
        exp = np.exp(scaled)
        return exp / exp.sum(axis=1, keepdims=True)


def load_crop_model(folder: Path, threads: int) -> CropModel:
    metadata_path, model_path = folder / "metadata.json", folder / "model.onnx"
    if not metadata_path.is_file() or not model_path.is_file():
        raise ModelLoadError(f"{folder.name}: model.onnx and metadata.json are required.")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        labels = list(metadata["labels"])
        pre = metadata["preprocessing"]
        for key in ("resize", "center_crop", "mean", "std"):
            pre[key]  # noqa: B018 - presence check
        temperature = float(metadata.get("temperature", 1.0))
        crop = str(metadata["crop"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelLoadError(f"{folder.name}: invalid metadata.json ({exc}).") from exc

    unknown = [label for label in labels if label not in CATALOG]
    if unknown:
        raise ModelLoadError(f"{folder.name}: labels missing from the catalog: {unknown}")
    if temperature <= 0:
        raise ModelLoadError(f"{folder.name}: temperature must be positive.")

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    try:
        session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # onnxruntime raises several exception types
        raise ModelLoadError(f"{folder.name}: cannot load model.onnx ({type(exc).__name__}).") from exc

    outputs = session.get_outputs()[0].shape
    if outputs[-1] != len(labels):
        raise ModelLoadError(
            f"{folder.name}: model has {outputs[-1]} outputs but metadata lists {len(labels)} labels."
        )

    return CropModel(
        crop=crop,
        version=str(metadata.get("model_version", "unknown")),
        labels=labels,
        preprocessing=pre,
        temperature=temperature,
        metadata=metadata,
        session=session,
    )


def load_models(model_dir: Path, threads: int) -> dict[str, CropModel]:
    """Load every crop folder under model_dir. Broken folders are logged and skipped."""
    models: dict[str, CropModel] = {}
    if not model_dir.is_dir():
        logger.warning("Model directory %s does not exist; no models loaded.", model_dir)
        return models
    for folder in sorted(p for p in model_dir.iterdir() if p.is_dir()):
        try:
            model = load_crop_model(folder, threads)
        except ModelLoadError as exc:
            logger.error("Skipping model: %s", exc)
            continue
        models[model.crop] = model
        logger.info("Loaded %s model %s (%d classes)", model.crop, model.version, len(model.labels))
    return models
