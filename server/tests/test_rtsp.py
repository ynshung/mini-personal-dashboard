from io import BytesIO
from PIL import Image
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.rtsp import resize_frame, apply_circular_mask


def _make_image(w: int, h: int, color=(255, 0, 0)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def test_resize_frame_fill_square_output():
    img = _make_image(640, 480)
    result = resize_frame(img, "fill")
    assert result.size == (240, 240)


def test_resize_frame_fill_no_black_bars():
    # 16:9 image filled to 240x240 — all pixels should be the source color, not black
    img = _make_image(1280, 720, color=(200, 100, 50))
    result = resize_frame(img, "fill")
    # Center pixel should be non-black (source color preserved)
    assert result.getpixel((120, 120)) != (0, 0, 0)


def test_resize_frame_fit_square_output():
    img = _make_image(640, 480)
    result = resize_frame(img, "fit")
    assert result.size == (240, 240)


def test_resize_frame_fit_has_black_bars():
    # 16:9 → fit → should have black bars top/bottom
    img = _make_image(1280, 720, color=(200, 100, 50))
    result = resize_frame(img, "fit")
    # Top-left corner should be black (letterbox area)
    assert result.getpixel((0, 0)) == (0, 0, 0)


def test_apply_circular_mask_corners_black():
    img = _make_image(240, 240, color=(255, 255, 255))
    result = apply_circular_mask(img)
    # Corners should be masked to black
    assert result.getpixel((0, 0)) == (0, 0, 0)
    assert result.getpixel((239, 0)) == (0, 0, 0)
    assert result.getpixel((0, 239)) == (0, 0, 0)
    assert result.getpixel((239, 239)) == (0, 0, 0)


def test_apply_circular_mask_center_preserved():
    img = _make_image(240, 240, color=(255, 0, 0))
    result = apply_circular_mask(img)
    # Center pixel should not be masked
    assert result.getpixel((120, 120)) == (255, 0, 0)
