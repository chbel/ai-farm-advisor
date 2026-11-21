"""Upload validation and preprocessing."""

import io

import numpy as np
import pytest
from PIL import Image

from app.services.preprocess import (
    InvalidImageError,
    center_crop,
    load_image,
    resize_shorter_side,
    to_model_input,
)
from tests.conftest import image_bytes
from tests.model_factory import MEAN, STD

LIMITS = {"min_side": 128, "max_pixels": 40_000_000}


@pytest.mark.parametrize(
    "size,expected",
    [((400, 300), (341, 256)), ((300, 400), (256, 341)), ((500, 500), (256, 256)), ((1000, 257), (996, 256))],
)
def test_resize_matches_torchvision_rule(size, expected):
    # torchvision Resize(256): shorter side -> 256, longer side -> int(256 * long / short).
    assert resize_shorter_side(Image.new("RGB", size), 256).size == expected


def test_center_crop_box_matches_torchvision_rule():
    # 341 x 256 -> left = round((341 - 224) / 2) = 58 (Python rounds half to even), top = 16.
    image = Image.new("RGB", (341, 256))
    image.putpixel((58, 16), (255, 255, 255))
    cropped = center_crop(image, 224)
    assert cropped.size == (224, 224)
    assert cropped.getpixel((0, 0)) == (255, 255, 255)


def test_model_input_shape_and_normalization():
    array = to_model_input(Image.new("RGB", (400, 300), (255, 0, 128)), resize=256, crop=224,
                           mean=MEAN, std=STD)
    assert array.shape == (1, 3, 224, 224) and array.dtype == np.float32
    expected = [(1 - MEAN[0]) / STD[0], (0 - MEAN[1]) / STD[1], (128 / 255 - MEAN[2]) / STD[2]]
    assert array[0, :, 100, 100] == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepted_formats(fmt):
    image = load_image(image_bytes(fmt=fmt), **LIMITS)
    assert image.mode == "RGB" and image.size == (400, 300)


def test_exif_orientation_is_applied():
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 CW" as written by phone cameras
    data = image_bytes(size=(400, 300), exif=exif.tobytes())
    assert load_image(data, **LIMITS).size == (300, 400)


def test_grayscale_and_alpha_are_converted_to_rgb():
    buffer = io.BytesIO()
    Image.new("LA", (300, 300), (120, 200)).save(buffer, format="PNG")
    assert load_image(buffer.getvalue(), **LIMITS).mode == "RGB"


@pytest.mark.parametrize(
    "data,message",
    [
        (b"not an image at all", "not a valid image"),
        (image_bytes(fmt="GIF"), "Unsupported image format"),
        (image_bytes(size=(100, 400)), "too small"),
        (image_bytes()[:200], "not a valid image"),  # truncated JPEG
    ],
)
def test_rejected_uploads(data, message):
    with pytest.raises(InvalidImageError, match=message):
        load_image(data, **LIMITS)


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_resolution_limit():
    with pytest.raises(InvalidImageError):
        load_image(image_bytes(size=(1000, 1000)), min_side=128, max_pixels=500_000)
