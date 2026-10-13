"""asm-cli tools probe: read-only queries, every raw reply recorded."""

import asyncio
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.core import CoreEngine
from tests.test_hardware_eq import DEVICES, _config


def _engine(config):
    engine = MagicMock()
    engine.device_config = config
    engine.usb_device = MagicMock(idProduct=0x2290)
    engine._frame_taps = []
    engine.get_command_endpoint_address.return_value = 0
    return engine


def test_probe_records_every_frame_after_each_query_and_reads_features():
    """The reply may begin with a report id or the opcode depending on the
    family, so the probe keeps every frame of the window, and a `feature`
    query is answered by GET_REPORT with the spec's report id."""
    engine = _engine(_config("nova_pro_omni.yaml"))
    sent = []

    def send(frame, endpoint):
        sent.append(frame)
        for tap in engine._frame_taps:  # what listen_endpoint_loop would do
            tap.append([0x01, frame[1], 0x2A])
        return True

    engine.send_command.side_effect = send
    engine.get_feature_report.return_value = [0x01, 0x1A, 0x04]

    result = asyncio.run(CoreEngine.probe_device(engine, window=0))

    assert result["ok"] and result["product_id"] == "2290"
    queries = engine.device_config.probe_queries
    assert sent == [q["frame"] for q in queries]
    eq = next(r for r in result["replies"] if r["name"] == "read_radio_eq_settings")
    assert eq["frames"] == ["01 1a 2a"] and eq["feature"] == "01 1a 04"
    engine.get_feature_report.assert_any_call(0x01, 1036)
    assert engine._frame_taps == []  # nothing left listening


def test_probe_without_queries_says_so():
    engine = _engine(MagicMock(probe_queries=[]))
    assert asyncio.run(CoreEngine.probe_device(engine)) == {
        "ok": False, "error": "no_probe_queries"}


@pytest.mark.parametrize("profile", sorted(p.name for p in DEVICES.glob("*.yaml")))
def test_probe_queries_are_bare_queries(profile):
    """Only constants go out: a query frame is a report id and an opcode,
    at most a fixed selector byte — never a value that could set something."""
    for q in _config(profile).probe_queries:
        assert 1 <= len(q["frame"]) <= 2, q
        assert q["reply"] in ("interrupt", "feature")

