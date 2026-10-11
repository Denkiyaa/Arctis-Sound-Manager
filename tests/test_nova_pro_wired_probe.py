"""Pure helpers of scripts/reverse-engineering/nova_pro_wired_probe.py (no USB)."""
import importlib.util
from pathlib import Path

import pytest

_PATH = (Path(__file__).resolve().parent.parent
         / "scripts" / "reverse-engineering" / "nova_pro_wired_probe.py")
_spec = importlib.util.spec_from_file_location("nova_pro_wired_probe", _PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_pack_columns_size_and_columns():
    img = probe.make_test_image()
    data = probe.pack_columns(img)
    assert len(data) == 128 * 56 // 8 == 896
    # column 0 is the border: every row 0..51 lit, padding rows 52..55 dark
    assert data[:7] == bytes([0xFF] * 6 + [0x0F])


def test_split_into_two_halves():
    data = probe.pack_columns(probe.make_test_image())
    chunks = probe.build_draw_bitmap_chunks(0, 12, 128, 52, data)
    assert len(chunks) == 2
    assert all(len(c) == 521 for c in chunks)
    assert chunks[0][:6] == [0x06, 0x93, 0, 12, 64, 56]
    assert chunks[1][:6] == [0x06, 0x93, 64, 12, 64, 56]
    assert bytes(chunks[0][6:6 + 448]) + bytes(chunks[1][6:6 + 448]) == data
    assert not any(chunks[0][6 + 448:]) and not any(chunks[1][6 + 448:])


def test_single_chunk_when_small():
    chunks = probe.build_draw_bitmap_chunks(3, 4, 32, 16, bytes([1]) * 64)
    assert len(chunks) == 1 and len(chunks[0]) == 521
    assert chunks[0][:6] == [0x06, 0x93, 3, 4, 32, 16]


def test_wrong_data_size_rejected():
    with pytest.raises(ValueError):
        probe.build_draw_bitmap_chunks(0, 12, 128, 52, b"\x00" * 10)


def test_summarize_events():
    frames = [
        (0.000, bytes([0x07, 0x25, 10])),
        (0.010, bytes([0x07, 0x25, 11])),
        (0.020, bytes([0x07, 0x25, 12])),
        (0.500, bytes([0x07, 0x25, 14])),
        (0.600, bytes([0x06, 0x20, 0, 14])),
    ]
    s = probe.summarize_events(frames)
    assert s["wheel_events"] == 4 and s["frames_total"] == 5
    assert s["min_gap_ms"] == 10.0 and s["max_gap_ms"] == 480.0
    assert s["median_gap_ms"] == 10.0
    assert s["max_burst_50ms"] == 3
    assert (s["first_volume"], s["last_volume"]) == (10, 14)
    assert s["other_frames"] == {"06 20": 1}


def test_summarize_empty():
    s = probe.summarize_events([])
    assert s["wheel_events"] == 0 and s["min_gap_ms"] is None


def test_decode_audio_settings():
    frame = [0x06, 0x20, 1, 42, 2, 0, 4] + [20] * 9
    d = probe.decode_audio_settings(frame)
    assert d["volume_0_56"] == 42 and d["audio_input"].startswith("1")
    assert "high" in d["gain"]
    assert probe.decode_audio_settings([0x07, 0x25, 1]) == {}
