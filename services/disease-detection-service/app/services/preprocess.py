"""Upload validation and image preprocessing.

Preprocessing reproduces the training pipeline exactly (torchvision
`Resize(256)` -> `CenterCrop(224)` -> `ToTensor` -> `Normalize`), using only
Pillow and NumPy so the service needs no PyTorch.
"""

import io

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImageError(ValueError):
    """The upload is not an acceptable image. The message is safe to show users."""


def load_image(data: bytes, *, min_side: int, max_pixels: int) -> Image.Image:
    """Decode and validate an uploaded image; return it as upright RGB."""

    # Pillow raises DecompressionBombError above 2x this limit; we also check below.
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            width, height = probe.size
            probe.verify()  # structural check without decoding all pixels
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise InvalidImageError("The file is not a valid image.") from None

    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError("Unsupported image format. Use JPEG, PNG or WebP.")
    if width * height > max_pixels:
        raise InvalidImageError("Image resolution is too large.")
    if min(width, height) < min_side:
        raise InvalidImageError(
            f"Image is too small ({width}x{height}). Use a photo at least {min_side} px on each side."
        )

    try:
        image = Image.open(io.BytesIO(data))  # verify() leaves the file unusable; reopen
        image = ImageOps.exif_transpose(image)  # phone photos: apply the EXIF orientation
        return image.convert("RGB")
    except (OSError, ValueError):
        raise InvalidImageError("The image could not be decoded.") from None


def resize_shorter_side(image: Image.Image, size: int) -> Image.Image:
    """Same output size as torchvision Resize(size) for a PIL image."""
    width, height = image.size
    short, long = (width, height) if width <= height else (height, width)
    new_short, new_long = size, int(size * long / short)
    new_size = (new_short, new_long) if width <= height else (new_long, new_short)
    return image.resize(new_size, Image.Resampling.BILINEAR)


def center_crop(image: Image.Image, size: int) -> Image.Image:
    """Same crop box as torchvision CenterCrop(size)."""
    width, height = image.size
    top = int(round((height - size) / 2.0))
    left = int(round((width - size) / 2.0))
    return image.crop((left, top, left + size, top + size))


def to_model_input(image: Image.Image, *, resize: int, crop: int,
                   mean: list[float], std: list[float]) -> np.ndarray:
    """RGB PIL image -> float32 NCHW array, normalized like training."""
    image = center_crop(resize_shorter_side(image, resize), crop)
    array = np.asarray(image, dtype=np.float32) / 255.0  # HWC in [0, 1]
    array = (array - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return array.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
