"""Builds tiny, deterministic ONNX models for tests (no PyTorch needed).

Graph: image [N,3,H,W] -> mean over H,W -> [N,3] -> MatMul W [3,C] + b -> logits [N,C].
With the default weights, a red image strongly predicts label 0 and a green
image strongly predicts the last label; a grey image gives an uncertain result.
"""

import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

LABELS = [
    "tomato_bacterial_spot", "tomato_early_blight", "tomato_late_blight", "tomato_leaf_mold",
    "tomato_septoria_leaf_spot", "tomato_spider_mites", "tomato_target_spot",
    "tomato_yellow_leaf_curl_virus", "tomato_mosaic_virus", "tomato_healthy",
]
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def default_weights(n_classes: int) -> np.ndarray:
    weights = np.zeros((3, n_classes), dtype=np.float32)
    weights[0, 0] = 3.0  # red channel -> first label
    weights[1, n_classes - 1] = 3.0  # green channel -> last label (healthy)
    return weights


def build_model(n_outputs: int) -> onnx.ModelProto:
    weights = numpy_helper.from_array(default_weights(n_outputs), "W")
    bias = numpy_helper.from_array(np.zeros(n_outputs, dtype=np.float32), "b")
    axes = numpy_helper.from_array(np.array([2, 3], dtype=np.int64), "axes")
    graph = helper.make_graph(
        [
            helper.make_node("ReduceMean", ["image", "axes"], ["pooled"], keepdims=0),
            helper.make_node("MatMul", ["pooled", "W"], ["scores"]),
            helper.make_node("Add", ["scores", "b"], ["logits"]),
        ],
        "tiny_classifier",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["batch", 3, 224, 224])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", n_outputs])],
        initializer=[weights, bias, axes],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    return model


def write_model_folder(folder: Path, *, labels: list[str] | None = None, n_outputs: int | None = None,
                       temperature: float = 1.0, crop: str = "tomato") -> Path:
    labels = labels or LABELS
    folder.mkdir(parents=True, exist_ok=True)
    onnx.save(build_model(n_outputs or len(labels)), folder / "model.onnx")
    metadata = {
        "model_version": "test-model-1",
        "crop": crop,
        "labels": labels,
        "preprocessing": {"resize": 256, "center_crop": 224, "mean": MEAN, "std": STD},
        "temperature": temperature,
        "metrics": {
            "field_test_plantdoc": {"accuracy": 0.61},
            "lab_test_plantvillage": {"accuracy": 0.98},
        },
        "datasets": [{"name": "PlantVillage", "license": "CC BY-SA 3.0"}],
    }
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return folder
