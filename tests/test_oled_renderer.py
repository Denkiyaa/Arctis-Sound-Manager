# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for OledRenderer text measurement."""
import math
import pytest
from PIL import ImageFont

from arctis_sound_manager.oled_renderer import OledRenderer, _load_font


@pytest.fixture
def renderer():
    return OledRenderer()


@pytest.mark.parametrize("preset,sz", [
    ("Crimson Desert", 8),
    ("Crimson Desert", 16),
    ("Crimson Desert", 10),
    ("Flat", 8),
    ("Bass Boost Heavy", 8),
])
def test_measure_eq_text_ge_actual_pixels(renderer, preset, sz):
    """measure_eq_text must return >= the true last rendered pixel + 1."""
    from PIL import Image as _Image, ImageDraw as _IDraw
    font = _load_font(max(7, min(30, sz)))
    text = f"EQ: {preset}"
    wide = math.ceil(font.getlength(text)) + 32
    h = font.getbbox(text)[3] + 2
    img = _Image.new("1", (wide, h), color=0)
    _IDraw.Draw(img).text((0, 0), text, font=font, fill=1)
    last_x = max(
        (x for x in range(wide) for y in range(h) if img.getpixel((x, y))),
        default=-1,
    )
    true_width = last_x + 1
    result = renderer.measure_eq_text(preset, sz)
    assert result >= true_width, (
        f"measure_eq_text({preset!r}, {sz}) = {result} < true pixel width = {true_width}"
    )


@pytest.mark.parametrize("profile,sz", [
    ("Nova Pro Default", 8),
    ("Nova Pro Default", 16),
    ("Gaming", 8),
])
def test_measure_profile_text_ge_actual_pixels(renderer, profile, sz):
    """measure_profile_text must return >= the true last rendered pixel + 1."""
    from PIL import Image as _Image, ImageDraw as _IDraw
    font = _load_font(max(7, min(30, sz)))
    text = f"Profile: {profile}"
    wide = math.ceil(font.getlength(text)) + 32
    h = font.getbbox(text)[3] + 2
    img = _Image.new("1", (wide, h), color=0)
    _IDraw.Draw(img).text((0, 0), text, font=font, fill=1)
    last_x = max(
        (x for x in range(wide) for y in range(h) if img.getpixel((x, y))),
        default=-1,
    )
    true_width = last_x + 1
    result = renderer.measure_profile_text(profile, sz)
    assert result >= true_width


def test_measure_eq_crimson_desert_scroll_reaches_end(renderer):
    """At max_offset the last glyph pixel must be within the 128px canvas (x <= 127)."""
    preset = "Crimson Desert"
    sz = 8
    text_w = renderer.measure_eq_text(preset, sz)
    max_offset = text_w - (renderer.WIDTH - 2)   # formula from oled_manager

    if max_offset <= 0:
        pytest.skip("preset fits without scrolling at this font size")

    # At max scroll: draw origin = 1 - max_offset
    draw_origin_x = 1 - max_offset
    font = _load_font(sz)
    bbox = font.getbbox(f"EQ: {preset}")
    last_glyph_pixel_x = draw_origin_x + bbox[2] - 1
    assert last_glyph_pixel_x <= renderer.WIDTH - 1, (
        f"Last glyph pixel at x={last_glyph_pixel_x}, expected <= {renderer.WIDTH - 1}"
    )


@pytest.fixture
def pillow_without_sized_default(monkeypatch):
    """Make load_default() behave like Pillow < 10.1: no `size` keyword.

    That is what Ubuntu 22.04 / Pop!_OS 22.04 ship (Pillow 9.0.1), and it used to
    crash the daemon at startup (#154).
    """
    real_load_default = ImageFont.load_default

    def load_default_without_size(*args, **kwargs):
        if args or "size" in kwargs:
            raise TypeError("load_default() got an unexpected keyword argument 'size'")
        return real_load_default()

    monkeypatch.setattr(ImageFont, "load_default", load_default_without_size)
    _load_font.cache_clear()
    yield
    _load_font.cache_clear()


def test_load_font_falls_back_on_old_pillow(pillow_without_sized_default):
    """A font must still come back, and it must honour the requested size."""
    small = _load_font(8)
    big = _load_font(24)
    assert isinstance(small, ImageFont.FreeTypeFont), "expected a scalable fallback face"
    assert big.getlength("ASM") > small.getlength("ASM")


def test_renderer_works_on_old_pillow(pillow_without_sized_default):
    """Constructing and rendering must not raise — that was the #154 crash."""
    from arctis_sound_manager.weather_service import WeatherData

    renderer = OledRenderer()
    image, header_h = renderer.render_status_image(
        battery_percent=42, charging=False, time_str="12:34",
        active_profile="Nova Pro Default", eq_preset="Flat",
        mic_status="muted", show_eq_chat=True, eq_chat_preset="Clarity",
        # The city label is drawn with the small font, the one measurement path
        # that a sizeless bitmap font would still have broken.
        weather=WeatherData(temp=21.0, unit_label="°C", condition="Clear",
                            icon_id=0, city="Stockholm"),
    )
    assert image.size[0] == renderer.WIDTH
    assert header_h > 0
    assert renderer.render_splash_image()
    assert renderer.measure_eq_text("Crimson Desert", 8) > 0
