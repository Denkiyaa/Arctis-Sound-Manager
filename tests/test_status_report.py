# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""`asm-cli status --json`: the shape scripts and status bars rely on."""

import json
from unittest.mock import patch

from arctis_sound_manager import status_report
from arctis_sound_manager.constants import DBUS_STATUS_OBJECT_PATH

_STATUS = {
    'headset': {
        'headset_power_status': {'value': 'online', 'type': 'label'},
        'headset_battery_charge': {'value': 90, 'type': 'percentage'},
    },
    'mic': {'mic_status': {'value': 'muted', 'type': 'label'}},
}
_SETTINGS = {'device_name': 'Arctis Nova Pro Wireless',
             'vendor_id': '0x1038', 'product_id': '0x12e0'}
_CHANNELS = {'game': {'present': True, 'volume': 80, 'muted': False}}


def _daemon(status, settings):
    def call(path, _iface, _member, _timeout):
        return status if path == DBUS_STATUS_OBJECT_PATH else settings
    return call


def _collect(status=_STATUS, settings=_SETTINGS, channels=_CHANNELS):
    with patch.object(status_report, '_call_daemon', _daemon(status, settings)), \
         patch.object(status_report, '_channels', return_value=channels):
        return status_report.collect_status()


def test_status_is_flattened_across_gui_sections():
    result = _collect()
    assert result['status'] == {'headset_power_status': 'online',
                                'headset_battery_charge': 90,
                                'mic_status': 'muted'}


def test_power_is_normalized_across_vocabularies():
    # 'online' and 'on' are the two vocabularies for the same state (#124);
    # a script must not have to know which family its headset belongs to.
    assert _collect()['power'] == 'on'
    off = {'headset': {'headset_power_status': {'value': 'off', 'type': 'label'}}}
    assert _collect(status=off)['power'] == 'off'


def test_device_identity():
    assert _collect()['device'] == {'name': 'Arctis Nova Pro Wireless',
                                    'vendor_id': '0x1038', 'product_id': '0x12e0'}


def test_no_device_detected():
    result = _collect(status={}, settings={})
    assert result['daemon'] is True
    assert result['device'] is None
    assert result['power'] == 'unknown'
    assert result['status'] == {}


def test_daemon_down_still_yields_full_shape():
    result = _collect(status=None, settings=None)
    assert result['daemon'] is False
    assert set(result) == {'schema', 'daemon', 'device', 'power', 'status', 'channels'}
    assert result['channels'] == _CHANNELS
    json.dumps(result)


def test_text_rendering():
    lines = status_report.format_status(_collect())
    assert lines[0] == 'device  Arctis Nova Pro Wireless'
    assert '  headset_battery_charge: 90' in lines
    assert not any('headset_power_status' in line for line in lines)
    assert status_report.format_status(_collect(status=None)) == ['asm-daemon: not running']
