# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtWidgets import QComboBox


class NoWheelComboBox(QComboBox):
    """QComboBox that leaves the wheel to the page it sits on.

    A stock combo takes the wheel whenever the pointer is over it, and Qt
    reports the step as `activated` — the same signal as a deliberate pick. On
    a scrolling page that turns "scroll past the Game output picker" into
    "send Game to the next device", silently. The sliders on these pages
    already ignore the wheel for the same reason (_NoWheelSlider); a combo
    changes far more than a slider does when it moves by accident.

    The popup list, once opened, still scrolls normally: it is a separate
    widget and never sees this handler.
    """

    def wheelEvent(self, event):
        event.ignore()
