"""Receipt photo preparation: downscale and re-encode for the LLM call.

Spec references: pinned-decisions §7, sprint 4 pins ("Image handling"). Bytes live
only in the request scope -- `prepare_image` takes raw bytes, returns a
`PreparedImage` holding a base64 string, and the caller discards the original bytes
immediately after calling this. Nothing here logs image bytes or base64 (spec §1,
§14).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image

MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_DIMENSION = 1568
_JPEG_QUALITY = 85


class ImageTooLarge(Exception):
    """Raised before decoding when the input exceeds `MAX_IMAGE_BYTES`."""


@dataclass(frozen=True)
class PreparedImage:
    """A downscaled, JPEG-encoded image ready for the Anthropic `image` content block."""

    data_b64: str
    media_type: str
    width: int
    height: int


def prepare_image(data: bytes) -> PreparedImage:
    """Refuse >10 MB input before decoding; otherwise downscale (longest side <=1568px,
    aspect ratio preserved) and re-encode as JPEG quality 85.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageTooLarge(f"image is {len(data)} bytes, max {MAX_IMAGE_BYTES}")

    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > _MAX_DIMENSION:
        scale = _MAX_DIMENSION / longest
        new_width = max(1, round(width * scale))
        new_height = max(1, round(height * scale))
        image = image.resize((new_width, new_height), Image.LANCZOS)
        width, height = image.size

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    encoded = base64.standard_b64encode(buffer.getvalue()).decode("ascii")

    return PreparedImage(data_b64=encoded, media_type="image/jpeg", width=width, height=height)
