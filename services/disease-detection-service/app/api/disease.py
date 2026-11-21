from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import CurrentPrincipal
from app.services.catalog import CATALOG
from app.services.model import CropModel
from app.services.preprocess import InvalidImageError, load_image, to_model_input

router = APIRouter(prefix="/disease", tags=["Disease detection"])

SUPPORTED_CROPS = ("olive", "tomato")  # crops the platform knows about
TOP_K = 3

DISCLAIMER = (
    "This is an automated estimate from a photo, not a diagnosis. Several conditions look "
    "alike, and the model cannot see what is not visible on the leaf. Confirm with an "
    "agricultural technician or a laboratory before treating."
)


class Outcome(StrEnum):
    LIKELY = "likely"  # confident enough to suggest a condition
    UNCERTAIN = "uncertain"  # show candidates only; ask for a better photo / an expert


class Candidate(BaseModel):
    label: str
    name_en: str
    name_fr: str
    cause_type: str
    causal_agent: str | None
    probability: float = Field(description="Calibrated probability (temperature scaling), 0-1.")


class ModelInfo(BaseModel):
    crop: str
    version: str
    field_test_accuracy: float | None = Field(
        description="Accuracy on real field photos (PlantDoc test split) at training time."
    )
    lab_test_accuracy: float | None


class AnalysisResult(BaseModel):
    crop: str
    outcome: Outcome
    message: str
    top_prediction: Candidate
    candidates: list[Candidate]
    model: ModelInfo
    disclaimer: str = DISCLAIMER


def _models(request: Request) -> dict[str, CropModel]:
    return request.app.state.models


def _model_info(model: CropModel) -> ModelInfo:
    metrics = model.metadata.get("metrics", {})
    return ModelInfo(
        crop=model.crop,
        version=model.version,
        field_test_accuracy=metrics.get("field_test_plantdoc", {}).get("accuracy"),
        lab_test_accuracy=metrics.get("lab_test_plantvillage", {}).get("accuracy"),
    )


def _candidate(label: str, probability: float) -> Candidate:
    info = CATALOG[label]
    return Candidate(label=label, name_en=info.name_en, name_fr=info.name_fr,
                     cause_type=info.cause_type, causal_agent=info.causal_agent,
                     probability=round(probability, 4))


@router.get("/models", summary="Installed models, their accuracy and training data")
async def list_models(request: Request, _: CurrentPrincipal) -> dict:
    return {
        "supported_crops": list(SUPPORTED_CROPS),
        "installed": [
            {
                **_model_info(m).model_dump(),
                "classes": [_candidate(label, 0).model_dump(exclude={"probability"}) for label in m.labels],
                "datasets": m.metadata.get("datasets", []),
                "metrics": m.metadata.get("metrics", {}),
            }
            for m in _models(request).values()
        ],
    }


@router.post(
    "/analyze",
    response_model=AnalysisResult,
    responses={
        401: {"description": "Missing or invalid token."},
        413: {"description": "File too large."},
        415: {"description": "Not an accepted image."},
        422: {"description": "Invalid input or crop not supported yet."},
        503: {"description": "No model installed for this crop."},
    },
)
async def analyze(
    request: Request,
    _: CurrentPrincipal,
    image: Annotated[UploadFile, File(description="Leaf photo: JPEG, PNG or WebP.")],
    crop_type: Annotated[str, Form()] = "tomato",
) -> AnalysisResult:
    """Estimate the most likely leaf condition from a photo of one crop leaf."""

    crop = crop_type.strip().lower()
    if crop not in SUPPORTED_CROPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Unknown crop '{crop_type}'. Supported: {', '.join(SUPPORTED_CROPS)}.")
    model = _models(request).get(crop)
    if model is None:
        if crop == "olive":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Olive disease detection is not available yet: no openly licensed olive "
                "image dataset exists. It will be added once Tunisian field photos are collected.",
            )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"No {crop} model is installed on this server.")

    # Read at most MAX + 1 bytes so oversized uploads are rejected without buffering them.
    data = await image.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"Image larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file is empty.")

    try:
        picture = load_image(data, min_side=settings.MIN_IMAGE_SIDE_PX,
                             max_pixels=settings.MAX_IMAGE_PIXELS)
    except InvalidImageError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    pre = model.preprocessing
    batch = to_model_input(picture, resize=pre["resize"], crop=pre["center_crop"],
                           mean=pre["mean"], std=pre["std"])
    probs = model.probabilities(batch)[0]

    order = probs.argsort()[::-1]
    candidates = [_candidate(model.labels[i], float(probs[i])) for i in order[:TOP_K]]
    top = candidates[0]
    margin = top.probability - (candidates[1].probability if len(candidates) > 1 else 0.0)
    confident = top.probability >= settings.MIN_CONFIDENCE and margin >= settings.MIN_MARGIN

    if not confident:
        outcome = Outcome.UNCERTAIN
        message = ("The model is not confident. Retake a sharp, well-lit photo of a single "
                   "affected leaf filling the frame, or ask a technician to inspect the plant.")
    elif top.label.endswith("_healthy"):
        outcome = Outcome.LIKELY
        message = "No disease symptoms recognized on this leaf. Check other leaves and plants too."
    else:
        outcome = Outcome.LIKELY
        message = (f"Symptoms resemble {top.name_en} ({top.probability:.0%} estimated probability). "
                   "Confirm before treating.")

    return AnalysisResult(
        crop=crop,
        outcome=outcome,
        message=message,
        top_prediction=top,
        candidates=candidates,
        model=_model_info(model),
    )
