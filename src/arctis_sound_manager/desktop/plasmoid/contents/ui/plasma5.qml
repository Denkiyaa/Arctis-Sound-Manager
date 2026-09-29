/*
    SPDX-FileCopyrightText: 2026 loteran
    SPDX-License-Identifier: GPL-3.0-or-later

    What Plasma 5 loads instead of main.qml, through X-Plasma-MainScript
    (Plasma 6 ignores that key and always loads main.qml). The real widget
    needs Plasma 6 APIs, so here it only explains why it cannot run.
    Plasma 5 still lists the widget: 5.27 does not filter on
    X-Plasma-API-Minimum-Version.
*/
import QtQuick 2.15
import QtQuick.Layouts 1.15

import org.kde.plasma.core 2.0 as PlasmaCore
import org.kde.plasma.components 3.0 as PlasmaComponents3
import org.kde.plasma.extras 2.0 as PlasmaExtras
import org.kde.plasma.plasmoid 2.0

import "tr.js" as T

Item {
    Plasmoid.icon: "audio-headset"
    Plasmoid.toolTipMainText: T.tr("plasma5_title")
    Plasmoid.toolTipSubText: T.tr("plasma5_body")

    Plasmoid.fullRepresentation: ColumnLayout {
        Layout.preferredWidth: PlasmaCore.Units.gridUnit * 18
        Layout.minimumWidth: PlasmaCore.Units.gridUnit * 12
        spacing: PlasmaCore.Units.largeSpacing

        PlasmaCore.IconItem {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: PlasmaCore.Units.iconSizes.huge
            Layout.preferredHeight: PlasmaCore.Units.iconSizes.huge
            source: "dialog-warning"
        }
        PlasmaExtras.Heading {
            Layout.fillWidth: true
            level: 3
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: T.tr("plasma5_title")
        }
        PlasmaComponents3.Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: T.tr("plasma5_body")
        }
    }
}
