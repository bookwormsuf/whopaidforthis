"""Tests for app.services.images.prepare_image (sprint 4 pins)."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.services.images import MAX_IMAGE_BYTES, ImageTooLarge, prepare_image


def _jpeg_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(120, 80, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def test_refuses_images_over_10mb() -> None:
    oversized = b"0" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ImageTooLarge):
        prepare_image(oversized)


def test_small_image_is_not_resized() -> None:
    data = _jpeg_bytes(400, 300)
    prepared = prepare_image(data)
    assert prepared.width == 400
    assert prepared.height == 300
    assert prepared.media_type == "image/jpeg"


def test_downscale_keeps_aspect_ratio_landscape() -> None:
    data = _jpeg_bytes(3136, 1568)  # 2:1, longest side exactly 2x the cap
    prepared = prepare_image(data)
    assert prepared.width == 1568
    assert prepared.height == 784
    assert prepared.height / prepared.width == pytest.approx(1568 / 3136, rel=1e-3)


def test_downscale_keeps_aspect_ratio_portrait() -> None:
    data = _jpeg_bytes(1200, 3200)  # 3:8, over the cap in height
    prepared = prepare_image(data)
    assert prepared.height == 1568
    assert prepared.width == round(1200 * (1568 / 3200))


def test_output_is_valid_base64_jpeg() -> None:
    data = _jpeg_bytes(2000, 2000)
    prepared = prepare_image(data)
    decoded = base64.standard_b64decode(prepared.data_b64)
    with Image.open(io.BytesIO(decoded)) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 1568
