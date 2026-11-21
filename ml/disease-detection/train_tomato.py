# %% [markdown]
# # AI Farm Advisor: tomato leaf disease classifier (training)
#
# Run in **Google Colab** with a GPU runtime (Runtime > Change runtime type > T4 GPU),
# then **Runtime > Run all** (about 20-40 minutes on a T4). At the end
# `tomato_disease_model.zip` is downloaded: extract it into
# `services/disease-detection-service/models/tomato/` and restart the service.
#
# **Model:** MobileNetV3-Large from torchvision (BSD-3-Clause), ImageNet-pretrained,
# fine-tuned for 10 tomato classes, exported to ONNX for CPU inference.
#
# **Data (licenses verified at source):**
# - PlantVillage (Mohanty, Hughes & Salathé, 2016), CC BY-SA 3.0: lab photos of
#   single leaves on plain backgrounds. github.com/spMohanty/PlantVillage-Dataset
# - PlantDoc (Singh et al., 2019), CC BY 4.0: real field photos.
#   github.com/pratikkayal/PlantDoc-Dataset
#
# Models trained only on PlantVillage perform much worse on field photos, so
# PlantDoc field images are mixed into training (upweighted) and the PlantDoc
# test split is reported **separately**: it is the number that matters for farmers.
# PlantDoc has no target spot class, and no spider mite images in its test split.

# %%
# Configuration. Everything that affects the result is here.
SEED = 42
IMAGE_SIZE = 224
RESIZE = 256
BATCH_SIZE = 64
HEAD_EPOCHS = 2  # classifier only, backbone frozen
FINETUNE_EPOCHS = 8  # whole network
HEAD_LR = 1e-3
FINETUNE_LR = 3e-4
WEIGHT_DECAY = 1e-4
LABEL_SMOOTHING = 0.1
FIELD_WEIGHT = 4.0  # sampling weight of field photos relative to lab photos
NUM_WORKERS = 2
DATA_DIR = "/content/data"
OUT_DIR = "/content/tomato_disease_model"
SMOKE_TEST = False  # True: tiny run on synthetic data (used to test this script)

MEAN = [0.485, 0.456, 0.406]  # ImageNet normalization (torchvision weights)
STD = [0.229, 0.224, 0.225]

# Our label -> (PlantVillage folder, PlantDoc folder or None). Order defines model outputs.
CLASSES = {
    "tomato_bacterial_spot": ("Tomato___Bacterial_spot", "Tomato leaf bacterial spot"),
    "tomato_early_blight": ("Tomato___Early_blight", "Tomato Early blight leaf"),
    "tomato_late_blight": ("Tomato___Late_blight", "Tomato leaf late blight"),
    "tomato_leaf_mold": ("Tomato___Leaf_Mold", "Tomato mold leaf"),
    "tomato_septoria_leaf_spot": ("Tomato___Septoria_leaf_spot", "Tomato Septoria leaf spot"),
    "tomato_spider_mites": ("Tomato___Spider_mites Two-spotted_spider_mite",
                            "Tomato two spotted spider mites leaf"),
    "tomato_target_spot": ("Tomato___Target_Spot", None),
    "tomato_yellow_leaf_curl_virus": ("Tomato___Tomato_Yellow_Leaf_Curl_Virus",
                                      "Tomato leaf yellow virus"),
    "tomato_mosaic_virus": ("Tomato___Tomato_mosaic_virus", "Tomato leaf mosaic virus"),
    "tomato_healthy": ("Tomato___healthy", "Tomato leaf"),
}
LABELS = list(CLASSES)

# %%
import json
import os
import random
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms

random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", DEVICE, torch.cuda.get_device_name(0) if DEVICE.type == "cuda" else "")
if DEVICE.type != "cuda" and not SMOKE_TEST:
    print("WARNING: no GPU. In Colab: Runtime > Change runtime type > T4 GPU.")

# %% [markdown]
# ## 1. Download the data (sparse git checkouts: only the tomato folders)

# %%
def sparse_clone(repo_url: str, target: Path, folders: list[str]) -> None:
    if target.exists():
        print("Already downloaded:", target)
        return
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                    repo_url, str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "sparse-checkout", "set", "--no-cone", *folders],
                   check=True)


def make_synthetic_data(root: Path) -> None:
    """Tiny random images with the real folder layout (SMOKE_TEST only)."""
    rng = np.random.default_rng(SEED)
    for pv_folder, pd_folder in CLASSES.values():
        targets = [(root / "plantvillage/raw/color" / pv_folder, 12)]
        if pd_folder:
            targets += [(root / "plantdoc/train" / pd_folder, 4), (root / "plantdoc/test" / pd_folder, 2)]
        for folder, count in targets:
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(count):
                pixels = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
                Image.fromarray(pixels).save(folder / f"img_{i}.jpg")


data_root = Path(DATA_DIR)
if SMOKE_TEST:
    make_synthetic_data(data_root)
else:
    sparse_clone("https://github.com/spMohanty/PlantVillage-Dataset.git",
                 data_root / "plantvillage", [f"raw/color/{pv}/" for pv, _ in CLASSES.values()])
    sparse_clone("https://github.com/pratikkayal/PlantDoc-Dataset.git", data_root / "plantdoc",
                 [f"{split}/{pd}/" for _, pd in CLASSES.values() if pd for split in ("train", "test")])

# %% [markdown]
# ## 2. Index images and split
# PlantVillage: stratified 80/10/10. PlantDoc: its own train/test split; 15 % of
# PlantDoc-train is held out for validation so calibration also sees field photos.

# %%
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def list_images(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)


def stratified_split(items: list, fractions: tuple[float, ...]) -> list[list]:
    by_label: dict[int, list] = {}
    for item in items:
        by_label.setdefault(item[1], []).append(item)
    splits: list[list] = [[] for _ in fractions]
    for group in by_label.values():
        random.shuffle(group)
        start = 0
        for i, fraction in enumerate(fractions):
            end = len(group) if i == len(fractions) - 1 else start + round(len(group) * fraction)
            splits[i].extend(group[start:end])
            start = end
    return splits


pv_items, pd_train_items, pd_test_items = [], [], []
for index, (pv_folder, pd_folder) in enumerate(CLASSES.values()):
    pv_items += [(p, index, "lab") for p in list_images(data_root / "plantvillage/raw/color" / pv_folder)]
    if pd_folder:
        pd_train_items += [(p, index, "field") for p in list_images(data_root / "plantdoc/train" / pd_folder)]
        pd_test_items += [(p, index, "field") for p in list_images(data_root / "plantdoc/test" / pd_folder)]

pv_train, pv_val, pv_test = stratified_split(pv_items, (0.8, 0.1, 0.1))
pd_train, pd_val = stratified_split(pd_train_items, (0.85, 0.15))
train_items, val_items = pv_train + pd_train, pv_val + pd_val

for name, items in [("PlantVillage", pv_items), ("PlantDoc train", pd_train_items),
                    ("PlantDoc test", pd_test_items)]:
    counts = Counter(LABELS[label] for _, label, _ in items)
    print(f"{name}: {len(items)} images", dict(sorted(counts.items())))
assert pv_items and pd_train_items and pd_test_items, "Data download failed: no images found."

# %% [markdown]
# ## 3. Data pipeline
# Strong augmentation (crops, flips, rotation, colour) narrows the gap between
# lab photos and field conditions.

# %%
train_tf = transforms.Compose([
    transforms.RandomResizedCrop(IMAGE_SIZE, scale=(0.5, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomVerticalFlip(),
    transforms.RandomRotation(20),
    transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.05),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])
# Must match the inference preprocessing in the Disease Detection Service.
eval_tf = transforms.Compose([
    transforms.Resize(RESIZE),
    transforms.CenterCrop(IMAGE_SIZE),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])


class ImageDataset(Dataset):
    def __init__(self, items: list, transform) -> None:
        self.items, self.transform = items, transform

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int):
        path, label, _ = self.items[i]
        with Image.open(path) as img:
            return self.transform(img.convert("RGB")), label


# Balance classes and give field photos more weight than lab photos.
class_counts = Counter(label for _, label, _ in train_items)
weights = [(FIELD_WEIGHT if domain == "field" else 1.0) / class_counts[label]
           for _, label, domain in train_items]
sampler = WeightedRandomSampler(weights, num_samples=len(train_items), replacement=True)

loader_args = dict(batch_size=BATCH_SIZE, num_workers=0 if SMOKE_TEST else NUM_WORKERS,
                   pin_memory=DEVICE.type == "cuda")
train_loader = DataLoader(ImageDataset(train_items, train_tf), sampler=sampler, **loader_args)


def eval_loader(items: list) -> DataLoader:
    return DataLoader(ImageDataset(items, eval_tf), shuffle=False, **loader_args)

# %% [markdown]
# ## 4. Model and training

# %%
weights_enum = None if SMOKE_TEST else models.MobileNet_V3_Large_Weights.IMAGENET1K_V2
model = models.mobilenet_v3_large(weights=weights_enum)
model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(LABELS))
model = model.to(DEVICE)
criterion = nn.CrossEntropyLoss(label_smoothing=LABEL_SMOOTHING)
use_amp = DEVICE.type == "cuda"
scaler = torch.amp.GradScaler("cuda", enabled=use_amp)


@torch.no_grad()
def predict_logits(items: list) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    all_logits, all_labels = [], []
    for images, labels in eval_loader(items):
        with torch.autocast(DEVICE.type, enabled=use_amp):
            all_logits.append(model(images.to(DEVICE)).float().cpu())
        all_labels.append(labels)
    return torch.cat(all_logits).numpy(), torch.cat(all_labels).numpy()


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, present: list[int]) -> float:
    scores = []
    for c in present:
        tp = np.sum((y_pred == c) & (y_true == c))
        fp = np.sum((y_pred == c) & (y_true != c))
        fn = np.sum((y_pred != c) & (y_true == c))
        scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(scores))


def run_epochs(epochs: int, lr: float, params) -> None:
    global best_f1
    optimizer = torch.optim.AdamW(params, lr=lr, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    for epoch in range(epochs):
        model.train()
        start, running, seen = time.time(), 0.0, 0
        for images, labels in train_loader:
            images, labels = images.to(DEVICE), labels.to(DEVICE)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(DEVICE.type, enabled=use_amp):
                loss = criterion(model(images), labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            running += loss.item() * len(labels)
            seen += len(labels)
        scheduler.step()

        logits, y = predict_logits(val_items)
        f1 = macro_f1(y, logits.argmax(1), sorted(set(y.tolist())))
        print(f"epoch {epoch + 1}/{epochs} loss {running / seen:.4f} "
              f"val macro-F1 {f1:.4f} ({time.time() - start:.0f}s)")
        if f1 > best_f1:
            best_f1 = f1
            torch.save(model.state_dict(), "best.pt")


best_f1 = -1.0
# Phase A: train only the new classifier (backbone frozen).
for p in model.features.parameters():
    p.requires_grad = False
run_epochs(1 if SMOKE_TEST else HEAD_EPOCHS, HEAD_LR, [p for p in model.parameters() if p.requires_grad])
# Phase B: fine-tune the whole network.
for p in model.parameters():
    p.requires_grad = True
run_epochs(1 if SMOKE_TEST else FINETUNE_EPOCHS, FINETUNE_LR, model.parameters())
model.load_state_dict(torch.load("best.pt", map_location=DEVICE))
print("Best validation macro-F1:", round(best_f1, 4))

# %% [markdown]
# ## 5. Confidence calibration (temperature scaling, Guo et al., 2017)
# Neural networks are often over-confident. A single temperature T, fitted on the
# validation set, makes softmax(logits / T) closer to real accuracy.

# %%
def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    logits_t, labels_t = torch.tensor(logits), torch.tensor(labels)
    log_t = torch.zeros(1, requires_grad=True)  # optimise log(T) so T stays positive
    optimizer = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)

    def closure():
        optimizer.zero_grad()
        loss = nn.functional.cross_entropy(logits_t / log_t.exp(), labels_t)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_t.exp().item())


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray, bins: int = 15) -> float:
    confidence, predicted = probs.max(1), probs.argmax(1)
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence > low) & (confidence <= high)
        if mask.any():
            ece += mask.mean() * abs((predicted[mask] == labels[mask]).mean() - confidence[mask].mean())
    return float(ece)


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


val_logits, val_labels = predict_logits(val_items)
TEMPERATURE = fit_temperature(val_logits, val_labels)
print(f"Temperature: {TEMPERATURE:.3f}  ECE before {expected_calibration_error(softmax(val_logits), val_labels):.4f}"
      f"  after {expected_calibration_error(softmax(val_logits / TEMPERATURE), val_labels):.4f}")

# %% [markdown]
# ## 6. Evaluation: lab test set and **field** test set, reported separately

# %%
def evaluate(items: list, name: str) -> dict:
    logits, y = predict_logits(items)
    probs = softmax(logits / TEMPERATURE)
    pred = probs.argmax(1)
    present = sorted(set(y.tolist()))
    per_class = {}
    for c in present:
        tp = int(np.sum((pred == c) & (y == c)))
        support = int(np.sum(y == c))
        predicted = int(np.sum(pred == c))
        per_class[LABELS[c]] = {
            "support": support,
            "precision": round(tp / predicted, 4) if predicted else 0.0,
            "recall": round(tp / support, 4),
        }
    confusion = np.zeros((len(LABELS), len(LABELS)), dtype=int)
    for t, p in zip(y, pred):
        confusion[t, p] += 1
    result = {
        "images": int(len(y)),
        "accuracy": round(float((pred == y).mean()), 4),
        "macro_f1": round(macro_f1(y, pred, present), 4),
        "expected_calibration_error": round(expected_calibration_error(probs, y), 4),
        "classes_evaluated": [LABELS[c] for c in present],
        "per_class": per_class,
        "confusion_matrix": confusion.tolist(),
    }
    print(f"\n{name}: accuracy {result['accuracy']}, macro-F1 {result['macro_f1']}, "
          f"ECE {result['expected_calibration_error']} ({result['images']} images)")
    for label, stats in per_class.items():
        print(f"  {label:32s} recall {stats['recall']:.3f}  precision {stats['precision']:.3f}  n={stats['support']}")
    return result


metrics = {
    "lab_test_plantvillage": evaluate(pv_test, "Lab test (PlantVillage)"),
    "field_test_plantdoc": evaluate(pd_test_items, "Field test (PlantDoc)"),
}

# %% [markdown]
# ## 7. Export to ONNX, verify, and package

# %%
import importlib.util

# The ONNX exporter needs `onnx`; `onnxruntime` verifies the exported file.
missing = [pkg for pkg in ("onnx", "onnxruntime") if importlib.util.find_spec(pkg) is None]
if missing:
    subprocess.run(["pip", "install", "-q", *missing], check=True)
import onnxruntime as ort

out = Path(OUT_DIR)
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)

model.eval().float().cpu()
dummy = torch.randn(1, 3, IMAGE_SIZE, IMAGE_SIZE)
torch.onnx.export(model, dummy, out / "model.onnx", input_names=["image"], output_names=["logits"],
                  dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17,
                  dynamo=False)

session =ort.InferenceSession(str(out / "model.onnx"), providers=["CPUExecutionProvider"])
onnx_logits = session.run(None, {"image": dummy.numpy()})[0]
torch_logits = model(dummy).detach().numpy()
max_diff = float(np.abs(onnx_logits - torch_logits).max())
print("Max |ONNX - PyTorch| logit difference:", max_diff)
assert max_diff < 1e-3, "ONNX export does not match the PyTorch model."

metadata = {
    "model_version": datetime.now(timezone.utc).strftime("tomato-mnv3l-%Y%m%d-%H%M"),
    "crop": "tomato",
    "architecture": "torchvision mobilenet_v3_large (ImageNet IMAGENET1K_V2 pretrained), fine-tuned",
    "labels": LABELS,
    "preprocessing": {"resize": RESIZE, "center_crop": IMAGE_SIZE, "mean": MEAN, "std": STD,
                      "color": "RGB", "layout": "NCHW"},
    "temperature": round(TEMPERATURE, 4),
    "metrics": metrics,
    "training": {"seed": SEED, "head_epochs": HEAD_EPOCHS, "finetune_epochs": FINETUNE_EPOCHS,
                 "field_weight": FIELD_WEIGHT, "label_smoothing": LABEL_SMOOTHING,
                 "train_images": len(train_items), "val_images": len(val_items),
                 "smoke_test": SMOKE_TEST, "trained_at": datetime.now(timezone.utc).isoformat()},
    "datasets": [
        {"name": "PlantVillage", "license": "CC BY-SA 3.0",
         "citation": "Mohanty, Hughes & Salathe (2016). Using Deep Learning for Image-Based "
                     "Plant Disease Detection. Frontiers in Plant Science 7:1419.",
         "url": "https://github.com/spMohanty/PlantVillage-Dataset"},
        {"name": "PlantDoc", "license": "CC BY 4.0",
         "citation": "Singh et al. (2020). PlantDoc: A Dataset for Visual Plant Disease "
                     "Detection. CoDS-COMAD 2020.",
         "url": "https://github.com/pratikkayal/PlantDoc-Dataset"},
    ],
}
(out / "metadata.json").write_text(json.dumps(metadata, indent=2))
print(json.dumps({k: metadata[k] for k in ("model_version", "temperature")}, indent=2))

archive = shutil.make_archive("/content/tomato_disease_model", "zip", out)
print("Package:", archive)
if not SMOKE_TEST:
    try:
        from google.colab import files
        files.download(archive)
    except ImportError:
        print("Not running in Colab: download the zip manually.")
