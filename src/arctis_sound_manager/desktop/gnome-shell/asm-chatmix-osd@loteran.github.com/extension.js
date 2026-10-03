// Copyright (C) 2026 loteran
// SPDX-License-Identifier: GPL-3.0-or-later

// ChatMix OSD for GNOME Shell. Mutter has no layer-shell, so ASM's tray
// cannot float its own bar over a fullscreen game on GNOME Wayland; only the
// Shell can. This extension is a renderer and nothing more: the tray decides
// when to show the bar and sends everything it needs (position, labels,
// theme colours) as one JSON payload over D-Bus.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// Must match chatmix_osd.GNOME_OSD_* on the Python side.
const BUS_NAME = 'name.giacomofurlan.ArctisManager.Next.ChatMixOsd';
const OBJECT_PATH = '/name/giacomofurlan/ArctisManager/Next/ChatMixOsd';
const IFACE_XML = `
<node>
  <interface name="${BUS_NAME}">
    <method name="Show">
      <arg type="s" direction="in" name="payload"/>
    </method>
  </interface>
</node>`;

// Same geometry as the Qt OSD (chatmix_osd.py).
const HIDE_DELAY_MS = 1500;
const BOTTOM_MARGIN = 24;
const OSD_WIDTH = 440;
const TRACK_HEIGHT = 6;
const CAP_WIDTH = 26;
const CAP_HEIGHT = 24;

function parseColor(hex, fallback = [1, 1, 1]) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex ?? '');
    if (!m)
        return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255];
}

function roundedRect(cr, x, y, w, h, r) {
    cr.newSubPath();
    cr.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    cr.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    cr.arc(x + r, y + r, r, Math.PI, 3 * Math.PI / 2);
    cr.closePath();
}

// The track: channel colours banded over the left half, Chat's colour over
// the right half, static; only the cap moves (as on the mixer page, #269).
const ChatMixBar = GObject.registerClass(
class ChatMixBar extends St.DrawingArea {
    _init() {
        super._init({x_expand: true, height: CAP_HEIGHT});
        this._state = null;
    }

    setState(state) {
        this._state = state;
        this.queue_repaint();
    }

    vfunc_repaint() {
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();
        const state = this._state;
        if (state) {
            const left = CAP_WIDTH / 2;
            const span = width - CAP_WIDTH;
            const top = (height - TRACK_HEIGHT) / 2;

            cr.save();
            roundedRect(cr, left, top, span, TRACK_HEIGHT, TRACK_HEIGHT / 2);
            cr.clip();
            const colors = state.left_colors?.length ? state.left_colors : ['#ffffff'];
            const position = Math.max(0, Math.min(100, state.position ?? 50));
            if (state.fill) {
                // A plain volume (Master): the track filled up to the level.
                cr.setSourceRGB(...parseColor(state.chat_color));
                cr.rectangle(left, top, span, TRACK_HEIGHT);
                cr.fill();
                cr.setSourceRGB(...parseColor(colors[0]));
                cr.rectangle(left, top, span * position / 100, TRACK_HEIGHT);
                cr.fill();
            } else {
                const band = span / 2 / colors.length;
                colors.forEach((c, i) => {
                    cr.setSourceRGB(...parseColor(c));
                    cr.rectangle(left + i * band, top, band + 1, TRACK_HEIGHT);
                    cr.fill();
                });
                cr.setSourceRGB(...parseColor(state.chat_color));
                cr.rectangle(left + span / 2, top, span / 2, TRACK_HEIGHT);
                cr.fill();
            }
            cr.restore();

            const capX = left + span * position / 100 - CAP_WIDTH / 2;
            roundedRect(cr, capX, 0, CAP_WIDTH, CAP_HEIGHT, 5);
            cr.setSourceRGB(...parseColor(state.cap_color, [0.9, 0.9, 0.9]));
            cr.fillPreserve();
            cr.setSourceRGBA(0, 0, 0, 0.35);
            cr.setLineWidth(1);
            cr.stroke();
        }
        cr.$dispose();
    }
});

class ChatMixOsd {
    constructor() {
        this._box = new St.BoxLayout({
            width: OSD_WIDTH,
            reactive: false,
            can_focus: false,
            visible: false,
        });
        this._channelsLabel = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._bar = new ChatMixBar();
        this._chatLabel = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._box.add_child(this._channelsLabel);
        this._box.add_child(this._bar);
        this._box.add_child(this._chatLabel);
        // Like the Shell's own OSDs: above every window, fullscreen ones too.
        Main.uiGroup.add_child(this._box);
        this._hideId = 0;
        this._unredirectDisabled = false;
    }

    show(state) {
        const labelStyle = `color: ${state.text_color}; font-size: 9pt;`;
        this._box.style =
            `background-color: ${state.bg_color}; border: 1px solid ${state.border_color};` +
            ' border-radius: 12px; padding: 10px 18px; spacing: 10px;';
        this._channelsLabel.text = state.left_label ?? '';
        this._channelsLabel.style = labelStyle;
        this._chatLabel.text = state.right_label ?? '';
        this._chatLabel.style = labelStyle;
        this._bar.setState(state);

        this._place();
        this._setUnredirect(false);
        Main.uiGroup.set_child_above_sibling(this._box, null);
        this._box.show();

        if (this._hideId)
            GLib.source_remove(this._hideId);
        this._hideId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, HIDE_DELAY_MS, () => {
            this._hideId = 0;
            this._box.hide();
            this._setUnredirect(true);
            return GLib.SOURCE_REMOVE;
        });
    }

    _place() {
        const monitor = Main.layoutManager.currentMonitor ?? Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        const [, height] = this._box.get_preferred_height(OSD_WIDTH);
        this._box.set_position(
            Math.round(area.x + (area.width - OSD_WIDTH) / 2),
            Math.round(area.y + area.height - height - BOTTOM_MARGIN));
    }

    // A fullscreen game is scanned out directly, bypassing the Shell's
    // stage; the Shell's OSDs turn that off while they are up.
    _setUnredirect(enabled) {
        if (enabled === !this._unredirectDisabled)
            return;
        const compositor = global.compositor;
        if (compositor?.disable_unredirect) {
            if (enabled)
                compositor.enable_unredirect();
            else
                compositor.disable_unredirect();
        }
        this._unredirectDisabled = !enabled;
    }

    destroy() {
        if (this._hideId)
            GLib.source_remove(this._hideId);
        this._hideId = 0;
        this._setUnredirect(true);
        this._box.destroy();
    }
}

export default class AsmChatMixOsdExtension extends Extension {
    enable() {
        this._osd = new ChatMixOsd();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, {
            Show: payload => {
                try {
                    this._osd?.show(JSON.parse(payload));
                } catch (e) {
                    console.error(`ASM ChatMix OSD: bad payload: ${e}`);
                }
            },
        });
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._ownerId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        this._dbus?.unexport();
        this._dbus = null;
        this._osd?.destroy();
        this._osd = null;
    }
}
