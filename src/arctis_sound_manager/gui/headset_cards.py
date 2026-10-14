# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Building blocks of the Headset page: live status as a grid of tiles, and the
card each group of headset settings sits in.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

# Tiles per row. Four keeps a value like "Plugged in, not charging" on one
# line at the page's minimum width.
_TILE_COLUMNS = 4

# Status keys whose value is a level worth drawing, not just printing.
_BAR_KEYS = frozenset({'headset_battery_charge', 'charge_slot_battery_charge'})


def _card_qss(object_name: str) -> str:
    return f"""
        QFrame#{object_name} {{
            background-color: {_theme.c('BG_CARD')};
            border: 1px solid {_theme.c('BORDER')};
            border-radius: 12px;
        }}
    """


def card_contents_qss() -> str:
    """For a widget placed inside a card: the card's colour, not the page's."""
    return f"""
        QWidget {{ background-color: {_theme.c('BG_CARD')}; color: {_theme.c('TEXT_PRIMARY')}; }}
        QLabel  {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
    """


def _format_value(entry: dict) -> str:
    value = entry.get('value')
    if entry.get('type') == 'percentage':
        return f"{value}%"
    return I18n.translate('status_values', value)


class _StatusTile(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)

        self.value = QLabel()
        self.value.setWordWrap(True)
        layout.addWidget(self.value)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        layout.addStretch(1)
        self.apply_theme()

    def set_entry(self, key: str, entry: dict) -> None:
        self.caption.setText(I18n.translate('status', key))
        self.value.setText(_format_value(entry))
        show_bar = key in _BAR_KEYS and isinstance(entry.get('value'), int)
        self.bar.setVisible(show_bar)
        if show_bar:
            self.bar.setValue(max(0, min(100, entry['value'])))

    def apply_theme(self) -> None:
        self.setStyleSheet(_card_qss("statusTile"))
        self.caption.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent; border: none;")
        self.value.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 14pt; font-weight: bold;"
            " background: transparent; border: none;")
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background-color: {_theme.c('BG_BUTTON')}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background-color: {_theme.c('ACCENT')}; border-radius: 2px; }}
        """)


class StatusTiles(QWidget):
    """Every live status value the headset reports, one tile each.

    The keys are whatever the device profile maps, so nothing here names a
    particular headset: a Nova 7 gets battery, ChatMix and Bluetooth, a wired
    Nova 3 gets no tiles at all and the grid simply stays empty.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        for column in range(_TILE_COLUMNS):
            self._grid.setColumnStretch(column, 1)
        self._tiles: list[_StatusTile] = []
        self._status: dict | None = None

    def update_status(self, status: dict) -> None:
        if status == self._status:
            return
        self._status = status

        entries = [
            (key, entry)
            for category in (status or {}).values() if isinstance(category, dict)
            for key, entry in category.items() if isinstance(entry, dict)
        ]
        # Reuse tiles rather than rebuild them: status arrives every second.
        while len(self._tiles) < len(entries):
            tile = _StatusTile(self)
            index = len(self._tiles)
            self._grid.addWidget(tile, index // _TILE_COLUMNS, index % _TILE_COLUMNS)
            self._tiles.append(tile)
        for tile, (key, entry) in zip(self._tiles, entries):
            tile.set_entry(key, entry)
            tile.setVisible(True)
        for tile in self._tiles[len(entries):]:
            tile.setVisible(False)
        self.setVisible(bool(entries))

    def apply_theme(self) -> None:
        for tile in self._tiles:
            tile.apply_theme()


class SettingsCard(QFrame):
    """A titled card holding one group of settings."""

    def __init__(self, title: str, body: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.title)

        self.body = body
        layout.addWidget(body)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(_card_qss("settingsCard"))
        self.title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 13pt; font-weight: bold;"
            " background: transparent; border: none;")
        self.body.setStyleSheet(card_contents_qss())
        if hasattr(self.body, "apply_theme"):
            self.body.apply_theme()


def section_title(section: str | None) -> str:
    """Card title for a settings section of the device profile."""
    if section:
        title = I18n.translate('ui', f'headset_section_{section}')
        if title != f'headset_section_{section}':
            return title
    return I18n.translate('ui', 'device_settings')
