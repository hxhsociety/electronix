#!/usr/bin/env python3
# UKBFG — Unofficial KiCAD BGA Footprint Generator
#
# Original (Python 2.7, GTK3):
#   Copyright (c) 2017 Pratik M Tambe <enthusiasticgeek@gmail.com>
#   https://github.com/enthusiasticgeek
#   MIT License
#
# Modernised for Python 3.10+ / PyGObject 3.42+ — ElectroniX validation tooling.
#
# Changes from the 2017 original:
#   * Python 3 syntax: print() functions, file context managers, f-strings.
#   * Replaced the broken `for child in Gtk.Button(""): ...` markup hack with
#     Pango markup applied directly to button labels (works on every PyGObject).
#   * Removed deprecated `Gdk.Color.parse` + `modify_fg` calls — colour now
#     comes from Pango markup `<span foreground="...">…</span>`.
#   * `Gtk.HBox`/`Gtk.VBox`/`Gtk.HSeparator` → `Gtk.Box(orientation=…)` /
#     `Gtk.Separator(orientation=…)` (the V2 forms are deprecated).
#   * `Gtk.STOCK_*` button labels replaced with plain text (stock removed).
#   * Fixed undefined `SomeError` → `OSError`; file writes wrapped in `with`.
#   * Fixed `Gtk.AboutDialog(UKBFG, self)` → `Gtk.AboutDialog(transient_for=self)`.
#   * Removed never-called methods that were missing `self`.
#
# Output: KiCAD `.kicad_mod` footprint files for arbitrary BGA packages.
# Use these in KiCAD to lay out a test PCB, then export **IPC-2581**
# (File → Fabrication Outputs → IPC-2581) for ElectroniX import.

from __future__ import annotations

import datetime
import math
import os
import signal
import time

import gi  # type: ignore[import-not-found]

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402


# ─── Constants ────────────────────────────────────────────────────────────────


class MouseButtons:
    LEFT_BUTTON = 1
    CENTER_BUTTON = 2
    RIGHT_BUTTON = 3


# DEC alphabet (skips I, O, Q, S to avoid confusion with 1, 0, 0, 5)
COLUMN_LABELS = list("ABCDEFGHJKLMNPRTUVWXYZ")
ROW_LABELS = list(range(1, 23))


def _markup(text: str, *, color: str = "black", bold: bool = True) -> str:
    """Build a Pango-markup label string with optional colour + bold."""
    inner = f"<b>{text}</b>" if bold else text
    return f'<span foreground="{color}">{inner}</span>'


def _make_markup_button(text: str, color: str = "darkred") -> Gtk.Button:
    """Create a Gtk.Button whose internal Gtk.Label uses Pango markup."""
    btn = Gtk.Button()
    label = Gtk.Label()
    label.set_markup(_markup(text, color=color))
    label.set_use_markup(True)
    btn.add(label)
    return btn


def _make_markup_label(text: str, color: str = "darkgreen", *, bold: bool = True) -> Gtk.Label:
    """Create a Gtk.Label rendered with Pango markup."""
    label = Gtk.Label()
    label.set_markup(_markup(text, color=color, bold=bold))
    label.set_use_markup(True)
    return label


# ─── Main window ──────────────────────────────────────────────────────────────


class UKBFG(Gtk.Window):
    def __init__(self) -> None:
        super().__init__()

        # ── Signals ──────────────────────────────────────────────────────────
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, self.signal_handler)
        if hasattr(signal, "SIGUSR1"):  # SIGUSR1 doesn't exist on Windows
            signal.signal(signal.SIGUSR1, self.signal_handler)

        # ── State ────────────────────────────────────────────────────────────
        self.BEGIN_MOUSE_X = 0
        self.BEGIN_MOUSE_Y = 0
        self.END_MOUSE_X = 0
        self.END_MOUSE_Y = 0

        self.SCALING = 50
        self.PACKAGE = "BGA_PKG"
        self.NUM_PINS_LENGTH = 10
        self.NUM_PINS_WIDTH = 10
        self.LENGTH = 9.0  # mm — vertical
        self.WIDTH = 9.0   # mm — horizontal
        self.OFFSET_X = 100
        self.OFFSET_Y = 100
        self.BALL_PITCH = 0.8     # mm
        self.BALL_DIAMETER = 0.45  # mm
        self.RESULT = ""

        self.set_title("UNOFFICIAL KiCAD BGA FOOTPRINT GENERATOR [UKBFG]")
        self.set_position(Gtk.WindowPosition.CENTER)

        # Calculated parameters (recomputed every redraw)
        self.CALC_LENGTH = (self.NUM_PINS_LENGTH - 1) * self.BALL_PITCH
        self.CALC_WIDTH = (self.NUM_PINS_WIDTH - 1) * self.BALL_PITCH
        self.CALC_BALL_DIAMETER = self.BALL_DIAMETER

        self.connect("destroy", Gtk.main_quit)

        # ── Drawing area ─────────────────────────────────────────────────────
        self.darea = Gtk.DrawingArea()
        self.darea.set_size_request(
            int(self.BALL_PITCH * self.SCALING * self.NUM_PINS_WIDTH) + self.OFFSET_X + 50,
            int(self.BALL_PITCH * self.SCALING * self.NUM_PINS_LENGTH) + self.OFFSET_Y + 50,
        )
        self.darea.connect("draw", self.on_draw)
        self.darea.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
        )
        self.darea.connect("button-press-event", self.on_button_press)
        self.darea.connect("button-release-event", self.on_button_release)
        self.darea.connect("motion_notify_event", self.on_motion_notify_event)

        # Populated balls (list of [x, y] grid coords)
        self.populate: list[list[int]] = []

        # ── Layout: top = canvas, bottom = controls ──────────────────────────
        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.vbox.pack_start(self.darea, False, False, 0)

        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.vboxes = [Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2) for _ in range(7)]
        for vb in self.vboxes:
            self.hbox.pack_start(vb, False, False, 0)
        self.vbox.pack_start(self.hbox, False, False, 0)

        # Bind self.vbox1 .. self.vbox7 for backwards-compatible names
        (self.vbox1, self.vbox2, self.vbox3, self.vbox4,
         self.vbox5, self.vbox6, self.vbox7) = self.vboxes

        # ── Column 1 — ball pitch ────────────────────────────────────────────
        self.ball_pitch_label = _make_markup_label("Ball Pitch (mm)")
        self.ball_pitch_entry = Gtk.Entry()
        self.ball_pitch_entry.set_max_length(5)
        self.ball_pitch_entry.set_text(str(self.BALL_PITCH))
        self.ball_pitch_button = _make_markup_button("Update Ball Pitch")
        self.ball_pitch_mils_label = Gtk.Label()
        self.ball_pitch_mils_label.set_markup(
            _markup(f"{self.BALL_PITCH / 0.0254:.4f} mil", color="purple", bold=False))
        self.ball_pitch_mils_label.set_use_markup(True)

        self._pack(self.vbox1, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.ball_pitch_label, self.ball_pitch_entry,
            self.ball_pitch_button, self.ball_pitch_mils_label,
        ])
        self.ball_pitch_button.connect("clicked", self.on_ball_pitch_button)

        # ── Column 2 — ball diameter ─────────────────────────────────────────
        self.ball_diameter_label = _make_markup_label("Ball Diameter (mm)")
        self.ball_diameter_entry = Gtk.Entry()
        self.ball_diameter_entry.set_max_length(5)
        self.ball_diameter_entry.set_text(str(self.BALL_DIAMETER))
        self.ball_diameter_button = _make_markup_button("Update Ball Diameter")
        self.ball_diameter_mils_label = Gtk.Label()
        self.ball_diameter_mils_label.set_markup(
            _markup(f"{self.BALL_DIAMETER / 0.0254:.4f} mil", color="purple", bold=False))
        self.ball_diameter_mils_label.set_use_markup(True)

        self._pack(self.vbox2, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.ball_diameter_label, self.ball_diameter_entry,
            self.ball_diameter_button, self.ball_diameter_mils_label,
        ])
        self.ball_diameter_button.connect("clicked", self.on_ball_diameter_button)

        # ── Column 3 — IC dimensions ─────────────────────────────────────────
        self.ball_dimensions_label = _make_markup_label("IC Dimensions (mm) [W×L]")
        self.ball_dimensions_entry_width = Gtk.Entry()
        self.ball_dimensions_entry_width.set_max_length(5)
        self.ball_dimensions_entry_width.set_text(str(self.WIDTH))
        self.ball_dimensions_entry_length = Gtk.Entry()
        self.ball_dimensions_entry_length.set_max_length(5)
        self.ball_dimensions_entry_length.set_text(str(self.LENGTH))
        self.ball_dimensions_button = _make_markup_button("Update IC Dimensions")

        self._pack(self.vbox3, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.ball_dimensions_label,
            self.ball_dimensions_entry_width, self.ball_dimensions_entry_length,
            self.ball_dimensions_button,
        ])
        self.ball_dimensions_button.connect("clicked", self.on_ball_dimensions_button)

        # ── Column 4 — pins ──────────────────────────────────────────────────
        self.pins_label = _make_markup_label("Pins")
        self.pins_entry_width = Gtk.Entry()
        self.pins_entry_width.set_max_length(5)
        self.pins_entry_width.set_text(str(self.NUM_PINS_WIDTH))
        self.pins_entry_length = Gtk.Entry()
        self.pins_entry_length.set_max_length(5)
        self.pins_entry_length.set_text(str(self.NUM_PINS_LENGTH))
        self.pins_button = _make_markup_button("Update Pins")

        self._pack(self.vbox4, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.pins_label,
            self.pins_entry_width, self.pins_entry_length,
            self.pins_button,
        ])
        self.pins_button.connect("clicked", self.on_pins_button)

        # ── Column 5 — populate / depopulate ─────────────────────────────────
        self.populate_depopulate_balls_label = _make_markup_label(
            "Select an area to populate or depopulate\nBGA balls (drag with left mouse button)",
            color="blue",
        )
        self.populate_balls_button = _make_markup_button("Populate Balls")
        self.depopulate_balls_button = _make_markup_button("Depopulate Balls")

        self._pack(self.vbox5, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.populate_depopulate_balls_label,
            self.populate_balls_button, self.depopulate_balls_button,
        ])
        self.populate_balls_button.connect("clicked", self.on_populate_balls_button)
        self.depopulate_balls_button.connect("clicked", self.on_depopulate_balls_button)

        # ── Column 6 — magnification ─────────────────────────────────────────
        self.magnification_label = _make_markup_label("Magnification\n(visual only)")
        self.magnification_entry = Gtk.Entry()
        self.magnification_entry.set_max_length(5)
        self.magnification_entry.set_text(str(self.SCALING))
        self.magnification_button = _make_markup_button("Apply Magnification")

        self._pack(self.vbox6, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.magnification_label, self.magnification_entry,
            self.magnification_button,
        ])
        self.magnification_button.connect("clicked", self.on_magnification_button)

        # ── Column 7 — save / about / exit ───────────────────────────────────
        self.save_label = _make_markup_label("Footprint Output")
        self.package_entry = Gtk.Entry()
        self.package_entry.set_max_length(20)
        self.package_entry.set_text(self.PACKAGE)
        self.save_button = _make_markup_button("Save KiCAD Footprint")
        self.about_button = _make_markup_button("About UKBFG")
        self.exit_button = _make_markup_button("Exit UKBFG")

        self._pack(self.vbox7, [
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            self.save_label, self.package_entry,
            self.save_button, self.about_button, self.exit_button,
        ])
        self.save_button.connect("clicked", self.on_save_button)
        self.about_button.connect("clicked", self.on_about_button)
        self.exit_button.connect("clicked", self.on_exit_button)

        self.add(self.vbox)
        self.show_all()

        # Fully populate the grid by default
        for x in range(self.NUM_PINS_WIDTH):
            for y in range(self.NUM_PINS_LENGTH):
                self.populate.append([x, y])

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _pack(box: Gtk.Box, widgets: list[Gtk.Widget]) -> None:
        for w in widgets:
            box.pack_start(w, False, False, 0)

    def signal_handler(self, signum: int, frame: object) -> None:
        print(f"signal received: signum={signum}")

    # ── File save ────────────────────────────────────────────────────────────

    def on_save_button(self, _widget: Gtk.Button) -> bool:
        if self.LENGTH <= self.CALC_LENGTH or self.WIDTH <= self.CALC_WIDTH:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CANCEL,
                text="Error: length/width <= (#balls - 1) × pitch",
            )
            dialog.format_secondary_text(
                "Incorrect calculation. Update one or more of: length, width, ball pitch, pins.")
            dialog.run()
            dialog.destroy()
            return False

        self.PACKAGE = self.package_entry.get_text()
        dialog = Gtk.FileChooserDialog(
            title="Save KiCAD footprint",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Save", Gtk.ResponseType.OK,
        )

        filter_kicad = Gtk.FileFilter()
        filter_kicad.set_name("KiCAD footprint")
        filter_kicad.add_pattern("*.kicad_mod")
        dialog.add_filter(filter_kicad)

        filter_any = Gtk.FileFilter()
        filter_any.set_name("Any files")
        filter_any.add_pattern("*")
        dialog.add_filter(filter_any)

        try:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                return False

            kicad_filename = dialog.get_filename()
            if not kicad_filename.endswith(".kicad_mod"):
                kicad_filename += ".kicad_mod"

            if os.path.exists(kicad_filename):
                confirm = Gtk.MessageDialog(
                    transient_for=self,
                    modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.YES_NO,
                    text=f"The file {kicad_filename} exists.\nOverwrite it?",
                )
                confirm.format_secondary_text(f"Overwrite {kicad_filename}?")
                try:
                    if confirm.run() != Gtk.ResponseType.YES:
                        return False
                finally:
                    confirm.destroy()

            try:
                with open(kicad_filename, "w", encoding="utf-8") as f:
                    f.write(self.RESULT)
                print(f"Saved {kicad_filename}")
            except OSError as err:
                print(f"Could not save {kicad_filename}: {err}")
        finally:
            dialog.destroy()
        return False

    # ── Misc button handlers ─────────────────────────────────────────────────

    def on_exit_button(self, _widget: Gtk.Button) -> None:
        Gtk.main_quit()

    def on_about_button(self, _widget: Gtk.Button) -> None:
        about = Gtk.AboutDialog(transient_for=self, modal=True)
        about.set_program_name("Unofficial KiCAD BGA Footprint Generator (UKBFG)")
        about.set_version("Version: 0.4 (Python 3 port)")
        about.set_copyright(
            "Copyright (c) 2017 Pratik M Tambe <enthusiasticgeek@gmail.com>\n"
            "Python 3 port for ElectroniX validation tooling.")
        about.set_comments("Generate KiCAD BGA footprints; export IPC-2581 from KiCAD for ElectroniX.")
        about.set_website("https://github.com/enthusiasticgeek")
        try:
            about.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_size("UKBFG.png", 300, 185))
        except Exception:
            pass  # logo is optional
        about.run()
        about.destroy()

    # ── Populate / depopulate ────────────────────────────────────────────────

    def _balls_in_drag_box(self) -> list[tuple[int, int]]:
        """Return all (x, y) pin coords whose centre lies inside the drag rect.

        Handles drags in any direction (TL→BR, TR→BL, BL→TR, BR→TL).
        """
        x0 = min(self.BEGIN_MOUSE_X, self.END_MOUSE_X)
        x1 = max(self.BEGIN_MOUSE_X, self.END_MOUSE_X)
        y0 = min(self.BEGIN_MOUSE_Y, self.END_MOUSE_Y)
        y1 = max(self.BEGIN_MOUSE_Y, self.END_MOUSE_Y)
        if x0 == x1 or y0 == y1:
            return []

        coords: list[tuple[int, int]] = []
        step_x = (self.BALL_PITCH * self.NUM_PINS_WIDTH) * self.SCALING / self.NUM_PINS_WIDTH
        step_y = (self.BALL_PITCH * self.NUM_PINS_LENGTH) * self.SCALING / self.NUM_PINS_LENGTH
        for x in range(self.NUM_PINS_WIDTH):
            for y in range(self.NUM_PINS_LENGTH):
                px = x * step_x + self.OFFSET_X
                py = y * step_y + self.OFFSET_Y
                if x0 < px < x1 and y0 < py < y1:
                    coords.append((x, y))
        return coords

    def on_populate_balls_button(self, _widget: Gtk.Button) -> None:
        coords = self._balls_in_drag_box()
        if not coords:
            print("Not a rectangle! Please select a rectangle.")
        for x, y in coords:
            if [x, y] not in self.populate:
                self.populate.append([x, y])
        self.darea.queue_draw()

    def on_depopulate_balls_button(self, _widget: Gtk.Button) -> None:
        coords = self._balls_in_drag_box()
        if not coords:
            print("Not a rectangle! Please select a rectangle.")
        for x, y in coords:
            while [x, y] in self.populate:
                self.populate.remove([x, y])
        self.darea.queue_draw()

    # ── Parameter update handlers ────────────────────────────────────────────

    def update_area(self) -> None:
        self.darea.set_size_request(
            int(self.BALL_PITCH * self.SCALING * self.NUM_PINS_WIDTH) + self.OFFSET_X + 50,
            int(self.BALL_PITCH * self.SCALING * self.NUM_PINS_LENGTH) + self.OFFSET_Y + 50,
        )

    def on_ball_pitch_button(self, _widget: Gtk.Button) -> None:
        try:
            self.BALL_PITCH = float(self.ball_pitch_entry.get_text())
        except ValueError:
            print(f"Invalid pitch: {self.ball_pitch_entry.get_text()}")
            return
        self.ball_pitch_mils_label.set_markup(
            _markup(f"{self.BALL_PITCH / 0.0254:.4f} mil", color="purple", bold=False))
        self.update_area()
        self.darea.queue_draw()

    def on_ball_diameter_button(self, _widget: Gtk.Button) -> None:
        try:
            self.BALL_DIAMETER = float(self.ball_diameter_entry.get_text())
        except ValueError:
            print(f"Invalid diameter: {self.ball_diameter_entry.get_text()}")
            return
        self.ball_diameter_mils_label.set_markup(
            _markup(f"{self.BALL_DIAMETER / 0.0254:.4f} mil", color="purple", bold=False))
        self.update_area()
        self.darea.queue_draw()

    def on_ball_dimensions_button(self, _widget: Gtk.Button) -> None:
        try:
            length = float(self.ball_dimensions_entry_length.get_text())
            width = float(self.ball_dimensions_entry_width.get_text())
        except ValueError:
            print("Invalid dimensions (length and width must be numbers)")
            return
        self.LENGTH, self.WIDTH = length, width
        self.update_area()
        self.darea.queue_draw()

    def on_pins_button(self, _widget: Gtk.Button) -> None:
        try:
            pins_length = int(self.pins_entry_length.get_text())
            pins_width = int(self.pins_entry_width.get_text())
        except ValueError:
            print("Invalid pin counts (must be integers)")
            return
        self.NUM_PINS_LENGTH = pins_length
        self.NUM_PINS_WIDTH = pins_width
        self.update_area()
        self.darea.queue_draw()

    def on_magnification_button(self, _widget: Gtk.Button) -> None:
        try:
            self.SCALING = int(self.magnification_entry.get_text())
        except ValueError:
            print(f"Invalid magnification: {self.magnification_entry.get_text()}")
            return
        self.update_area()
        self.darea.queue_draw()

    # ── Mouse handlers ───────────────────────────────────────────────────────

    def on_button_press(self, _w: Gtk.Widget, e: Gdk.EventButton) -> None:
        if e.type == Gdk.EventType.BUTTON_PRESS and e.button == MouseButtons.LEFT_BUTTON:
            self.BEGIN_MOUSE_X = int(e.x)
            self.BEGIN_MOUSE_Y = int(e.y)
        self.darea.queue_draw()

    def on_button_release(self, _w: Gtk.Widget, e: Gdk.EventButton) -> None:
        if e.type == Gdk.EventType.BUTTON_RELEASE and e.button == MouseButtons.LEFT_BUTTON:
            self.END_MOUSE_X = int(e.x)
            self.END_MOUSE_Y = int(e.y)
        self.darea.queue_draw()

    def on_motion_notify_event(self, _w: Gtk.Widget, e: Gdk.EventMotion) -> None:
        if e.type == Gdk.EventType.MOTION_NOTIFY:
            self.END_MOUSE_X = int(e.x)
            self.END_MOUSE_Y = int(e.y)
        self.darea.queue_draw()

    # ── Cairo draw ───────────────────────────────────────────────────────────

    def on_draw(self, _widget: Gtk.DrawingArea, cr) -> bool:  # cr: cairo.Context
        # Recompute calc-derived values from current inputs
        self.CALC_LENGTH = (self.NUM_PINS_LENGTH - 1) * self.BALL_PITCH
        self.CALC_WIDTH = (self.NUM_PINS_WIDTH - 1) * self.BALL_PITCH
        self.CALC_BALL_DIAMETER = self.BALL_DIAMETER
        self.RESULT = ""

        import cairo  # local import — only needed during drawing

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_source_rgb(0.0, 0.0, 0.0)

        # Range check
        if (
            self.NUM_PINS_LENGTH < 2 or self.NUM_PINS_LENGTH > 22
            or self.NUM_PINS_WIDTH < 2 or self.NUM_PINS_WIDTH > 22
        ):
            cr.move_to(self.OFFSET_X - 99, self.OFFSET_Y - 40)
            cr.show_text("BGA pin count (width/length) must be in [2, 22]")
            return False

        step_x = (self.BALL_PITCH * self.NUM_PINS_WIDTH) * self.SCALING / self.NUM_PINS_WIDTH
        step_y = (self.BALL_PITCH * self.NUM_PINS_LENGTH) * self.SCALING / self.NUM_PINS_LENGTH

        # Column labels (Y axis)
        cr.set_source_rgb(0.0, 0.0, 1.0)
        for y in range(self.NUM_PINS_LENGTH):
            cr.move_to(self.OFFSET_X - 20, self.OFFSET_Y + self.BALL_PITCH * self.SCALING * y)
            cr.show_text(str(COLUMN_LABELS[y]))

        # Row labels (X axis)
        for x in range(self.NUM_PINS_WIDTH):
            cr.move_to(self.OFFSET_X + self.BALL_PITCH * self.SCALING * x, self.OFFSET_Y - 20)
            cr.show_text(str(ROW_LABELS[x]))

        # Pitch rectangle (corner-to-corner span of all balls)
        cr.set_source_rgb(0.8, 0.8, 0.8)
        cr.rectangle(
            self.OFFSET_X, self.OFFSET_Y,
            (self.NUM_PINS_WIDTH - 1) * step_x,
            (self.NUM_PINS_LENGTH - 1) * step_y,
        )
        cr.fill()

        # Outer package outline
        cr.set_source_rgb(0.8, 0.4, 0.4)
        cr.rectangle(
            self.OFFSET_X - (self.WIDTH - self.BALL_PITCH * self.NUM_PINS_WIDTH) / 2,
            self.OFFSET_Y - (self.LENGTH - self.BALL_PITCH * self.NUM_PINS_LENGTH) / 2,
            (self.NUM_PINS_WIDTH - 1) * step_x + (self.WIDTH - self.BALL_PITCH * self.NUM_PINS_WIDTH),
            (self.NUM_PINS_LENGTH - 1) * step_y + (self.LENGTH - self.BALL_PITCH * self.NUM_PINS_LENGTH),
        )
        cr.stroke()

        # Balls
        for x in range(self.NUM_PINS_WIDTH):
            for y in range(self.NUM_PINS_LENGTH):
                if [x, y] in self.populate:
                    cr.set_source_rgb(0.6, 0.6, 0.6)
                else:
                    cr.set_source_rgb(0.85, 0.85, 0.85)
                cr.arc(
                    x * step_x + self.OFFSET_X,
                    y * step_y + self.OFFSET_Y,
                    self.BALL_DIAMETER / 2 * self.SCALING,
                    0, 2 * math.pi,
                )
                cr.fill()

        # Ball labels
        for x in range(self.NUM_PINS_WIDTH):
            for y in range(self.NUM_PINS_LENGTH):
                cr.set_source_rgb(0.0, 0.0, 1.0) if [x, y] in self.populate else cr.set_source_rgb(0.85, 0.85, 0.85)
                cr.move_to(x * step_x + self.OFFSET_X, y * step_y + self.OFFSET_Y)
                cr.show_text(f"{COLUMN_LABELS[y]}{ROW_LABELS[x]}")

        # Annotation: pitch
        cr.set_source_rgb(1.0, 0.0, 0.0)
        cr.set_font_size(self.BALL_PITCH * 0.3 * self.SCALING)
        cr.move_to(self.OFFSET_X, (self.OFFSET_Y + step_y + self.OFFSET_Y) / 2)
        cr.show_text(f"  {self.BALL_PITCH} mm pitch")
        cr.set_line_width(2)
        cr.move_to(self.OFFSET_X, self.OFFSET_Y)
        cr.line_to(self.OFFSET_X, step_y + self.OFFSET_Y)
        cr.stroke()

        # Annotation: length
        cr.set_source_rgb(0.3, 0.4, 0.5)
        cr.move_to(
            self.OFFSET_X - 99,
            int(self.OFFSET_Y + (self.NUM_PINS_LENGTH - 1) * step_y + self.OFFSET_Y) / 2,
        )
        cr.show_text(f"  {self.LENGTH} mm length")
        cr.move_to(self.OFFSET_X - 40,
                   self.OFFSET_Y - (self.LENGTH - self.BALL_PITCH * self.NUM_PINS_LENGTH) / 2)
        cr.line_to(self.OFFSET_X - 40,
                   (self.NUM_PINS_LENGTH - 1) * step_y + self.OFFSET_Y
                   + (self.LENGTH - self.BALL_PITCH * self.NUM_PINS_LENGTH) / 2)
        cr.stroke()

        # Annotation: width
        cr.move_to(
            int(self.OFFSET_X + (self.NUM_PINS_WIDTH - 1) * step_x + self.OFFSET_X) / 2,
            self.OFFSET_Y - 40,
        )
        cr.show_text(f"  {self.WIDTH} mm width")
        cr.move_to(self.OFFSET_X - (self.WIDTH - self.BALL_PITCH * self.NUM_PINS_WIDTH) / 2,
                   self.OFFSET_Y - 40)
        cr.line_to((self.NUM_PINS_WIDTH - 1) * step_x + self.OFFSET_X
                   + (self.WIDTH - self.BALL_PITCH * self.NUM_PINS_WIDTH) / 2,
                   self.OFFSET_Y - 40)
        cr.stroke()

        # Annotation: diameter
        cr.set_source_rgb(0.3, 0.4, 0.0)
        cr.move_to(
            int(self.OFFSET_X + (self.NUM_PINS_WIDTH - 1) * step_x + self.OFFSET_X) / 2,
            self.OFFSET_Y - 80,
        )
        cr.show_text(f"  {self.BALL_DIAMETER} mm diameter")
        cr.arc(
            int(self.OFFSET_X + (self.NUM_PINS_WIDTH - 1) * step_x + self.OFFSET_X) / 2,
            self.OFFSET_Y - 60,
            self.BALL_DIAMETER / 2 * self.SCALING,
            0, 2 * math.pi,
        )
        cr.fill()

        # Drag-selection rectangle
        cr.set_source_rgb(0.3, 0.4, 0.5)
        cr.rectangle(
            self.BEGIN_MOUSE_X, self.BEGIN_MOUSE_Y,
            self.END_MOUSE_X - self.BEGIN_MOUSE_X,
            self.END_MOUSE_Y - self.BEGIN_MOUSE_Y,
        )
        cr.stroke()

        # ── Build the .kicad_mod text in self.RESULT ─────────────────────────
        dt = datetime.datetime.now()
        tedit = hex(int(time.mktime(dt.timetuple()))).upper().replace("0X", "")

        body = []
        body.append(
            f"(module BGA-{self.PACKAGE}_{self.NUM_PINS_WIDTH}x{self.NUM_PINS_LENGTH}_"
            f"{self.WIDTH}x{self.LENGTH}mm_Pitch{self.BALL_PITCH}mm "
            f"(layer F.Cu) (tedit {tedit})"
        )
        body.append(
            f"  (descr \"BGA-{self.PACKAGE}, {self.NUM_PINS_WIDTH}x{self.NUM_PINS_LENGTH}, "
            f"{self.WIDTH}x{self.LENGTH}mm package, pitch {self.BALL_PITCH}mm\")"
        )
        body.append(f"  (tags BGA-{self.PACKAGE})")
        body.append("  (attr smd)")
        body.append(f"  (fp_text reference REF** (at 0 -{self.LENGTH/2 + 1}) (layer F.SilkS)")
        body.append("    (effects (font (size 1 1) (thickness 0.15)))")
        body.append("  )")
        body.append(
            f"  (fp_text value {self.PACKAGE}_{self.NUM_PINS_WIDTH}x{self.NUM_PINS_LENGTH}_"
            f"{self.WIDTH}x{self.LENGTH}mm_Pitch{self.BALL_PITCH}mm "
            f"(at 0 {self.LENGTH/2 + 1}) (layer F.Fab)"
        )
        body.append("    (effects (font (size 1 1) (thickness 0.15)))")
        body.append("  )")

        # Top-left orientation marker
        body.append(
            f"  (fp_line (start -{self.WIDTH/2 + 0.1} -{self.LENGTH/2 - 1.70}) "
            f"(end -{self.WIDTH/2 + 0.1} -{self.LENGTH/2 + 0.1}) (layer F.SilkS) (width 0.12))"
        )
        body.append(
            f"  (fp_line (start -{self.WIDTH/2 + 0.1} -{self.LENGTH/2 + 0.1}) "
            f"(end -{self.WIDTH/2 - 1.70} -{self.LENGTH/2 + 0.1}) (layer F.SilkS) (width 0.12))"
        )

        # F.SilkS rectangle
        for sx, sy, ex, ey in [
            (self.WIDTH/2,   -self.LENGTH/2, -self.WIDTH/2, -self.LENGTH/2),
            (-self.WIDTH/2,  -self.LENGTH/2, -self.WIDTH/2,  self.LENGTH/2),
            (-self.WIDTH/2,   self.LENGTH/2,  self.WIDTH/2,  self.LENGTH/2),
            (self.WIDTH/2,    self.LENGTH/2,  self.WIDTH/2, -self.LENGTH/2),
        ]:
            body.append(
                f"  (fp_line (start {sx} {sy}) (end {ex} {ey}) (layer F.SilkS) (width 0.12))"
            )

        # F.Fab rectangle (slightly inset) + corner chamfer
        for sx, sy, ex, ey in [
            (self.WIDTH/2 - 0.1,  -self.LENGTH/2 + 0.1, -self.WIDTH/2 + 0.1, -self.LENGTH/2 + 0.1),
            (-self.WIDTH/2 + 0.1, -self.LENGTH/2 + 0.1, -self.WIDTH/2 + 0.1,  self.LENGTH/2 - 0.1),
            (-self.WIDTH/2 + 0.1,  self.LENGTH/2 - 0.1,  self.WIDTH/2 - 0.1,  self.LENGTH/2 - 0.1),
            (self.WIDTH/2 - 0.1,   self.LENGTH/2 - 0.1,  self.WIDTH/2 - 0.1, -self.LENGTH/2 + 0.1),
            (-self.WIDTH/2 + 0.1, -self.LENGTH/2 + 0.5, -self.WIDTH/2 + 0.5, -self.LENGTH/2 + 0.1),
        ]:
            body.append(
                f"  (fp_line (start {sx} {sy}) (end {ex} {ey}) (layer F.Fab) (width 0.1))"
            )

        # F.CrtYd rectangle (0.7 mm courtyard)
        for sx, sy, ex, ey in [
            (self.WIDTH/2 + 0.7,  -self.LENGTH/2 - 0.7, -self.WIDTH/2 - 0.7, -self.LENGTH/2 - 0.7),
            (-self.WIDTH/2 - 0.7, -self.LENGTH/2 - 0.7, -self.WIDTH/2 - 0.7,  self.LENGTH/2 + 0.7),
            (-self.WIDTH/2 - 0.7,  self.LENGTH/2 + 0.7,  self.WIDTH/2 + 0.7,  self.LENGTH/2 + 0.7),
            (self.WIDTH/2 + 0.7,   self.LENGTH/2 + 0.7,  self.WIDTH/2 + 0.7, -self.LENGTH/2 - 0.7),
        ]:
            body.append(
                f"  (fp_line (start {sx} {sy}) (end {ex} {ey}) (layer F.CrtYd) (width 0.05))"
            )

        # SMD pads for each populated ball
        for px_grid, py_grid in self.populate:
            if px_grid > self.NUM_PINS_WIDTH - 1 or py_grid > self.NUM_PINS_LENGTH - 1:
                continue
            pt_x = -self.CALC_WIDTH / 2 + px_grid * self.BALL_PITCH
            pt_y = -self.CALC_LENGTH / 2 + py_grid * self.BALL_PITCH
            pad_id = f"{COLUMN_LABELS[py_grid]}{ROW_LABELS[px_grid]}"
            body.append(
                f"(pad {pad_id} smd circle (at {pt_x} {pt_y}) "
                f"(size {self.CALC_BALL_DIAMETER} {self.CALC_BALL_DIAMETER}) "
                "(layers F.Cu F.Paste F.Mask))"
            )

        # 3D model placeholder (commented; uncomment when WRL exists)
        body.append("  # 3D model — uncomment when a .wrl file is available.")
        body.append(
            f"  # (model Housings_BGA.3dshapes/BGA-{self.PACKAGE}_"
            f"{self.NUM_PINS_WIDTH}x{self.NUM_PINS_LENGTH}_{self.WIDTH}x{self.LENGTH}mm_"
            f"Pitch{self.BALL_PITCH}mm.wrl"
        )
        body.append("  #   (at (xyz 0 0 0))")
        body.append("  #   (scale (xyz 1 1 1))")
        body.append("  #   (rotate (xyz 0 0 0))")
        body.append("  # )")
        body.append(")")

        self.RESULT = "\n".join(body) + "\n"
        return False


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    UKBFG()
    Gtk.main()


if __name__ == "__main__":
    main()
