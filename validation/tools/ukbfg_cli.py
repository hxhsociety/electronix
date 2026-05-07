#!/usr/bin/env python3
"""ukbfg_cli.py — headless KiCAD BGA footprint generator.

Pure stdlib — no GTK, no Cairo, no third-party packages.

Generates a `.kicad_mod` file for an arbitrary BGA package, suitable for
import into KiCAD where it can then be exported as IPC-2581 for ElectroniX.

Original GUI tool: UKBFG by Pratik M Tambe (MIT, 2017).
This CLI version distills the generation logic for ElectroniX validation.

Examples
--------
PBGA 256, 1.0 mm pitch (Darveaux 2000 validation case):
    python ukbfg_cli.py \
        --package PBGA256 \
        --pins 16x16 \
        --pitch 1.0 \
        --ball-diameter 0.60 \
        --ic-size 27x27 \
        --output bga256.kicad_mod

CSP 8×8, 0.8 mm pitch (Syed 2004 validation case):
    python ukbfg_cli.py \
        --package CSP064 \
        --pins 8x8 \
        --pitch 0.8 \
        --ball-diameter 0.45 \
        --ic-size 9x9 \
        --output csp64.kicad_mod
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

# DEC alphabet (skips I, O, Q, S to avoid confusion with 1, 0, 0, 5)
COLUMN_LABELS = list("ABCDEFGHJKLMNPRTUVWXYZ")
ROW_LABELS = list(range(1, 23))


# ─── Footprint generator ──────────────────────────────────────────────────────


def generate_kicad_mod(
    *,
    package: str,
    n_width: int,
    n_length: int,
    pitch_mm: float,
    ball_diam_mm: float,
    ic_width_mm: float,
    ic_length_mm: float,
    depopulate: list[tuple[int, int]] | None = None,
) -> str:
    """Return the `.kicad_mod` text for the requested BGA package.

    Coordinates: package centre at (0, 0). Balls form an n_width × n_length grid
    centred on the package, named per JEDEC convention (skip-letter rows × digits).

    `depopulate` is a list of (col_idx, row_idx) pairs to omit (e.g. die-attach
    voids in the centre of large BGAs).
    """
    # ── Sanity checks ────────────────────────────────────────────────────────
    if not (2 <= n_width <= 22 and 2 <= n_length <= 22):
        raise ValueError("Pin counts must be in [2, 22]")
    calc_width = (n_width - 1) * pitch_mm
    calc_length = (n_length - 1) * pitch_mm
    if ic_width_mm <= calc_width:
        raise ValueError(
            f"IC width ({ic_width_mm}) must exceed (n_width - 1) × pitch ({calc_width:.3f})")
    if ic_length_mm <= calc_length:
        raise ValueError(
            f"IC length ({ic_length_mm}) must exceed (n_length - 1) × pitch ({calc_length:.3f})")

    depopulate_set = set(depopulate or [])

    # ── Header — KiCAD 7/8/9 sexpr format ────────────────────────────────────
    tedit = hex(int(time.mktime(datetime.datetime.now().timetuple()))).upper().replace("0X", "")
    name = f"BGA-{package}_{n_width}x{n_length}_{ic_width_mm}x{ic_length_mm}mm_Pitch{pitch_mm}mm"

    lines: list[str] = []
    # Using `footprint` (not `module`) — `module` is the legacy KiCAD ≤4 keyword
    # that KiCAD 7+ rejects on import.
    lines.append(f'(footprint "{name}"')
    lines.append('  (version 20240108)')
    lines.append('  (generator "ukbfg_cli")')
    lines.append('  (layer "F.Cu")')
    lines.append(f'  (tedit {tedit})')
    lines.append(
        f'  (descr "BGA-{package}, {n_width}×{n_length}, '
        f'{ic_width_mm}×{ic_length_mm} mm package, pitch {pitch_mm} mm")'
    )
    lines.append(f'  (tags "BGA-{package}")')
    lines.append('  (attr smd)')
    lines.append(f'  (fp_text reference "REF**" (at 0 -{ic_length_mm/2 + 1}) (layer "F.SilkS")')
    lines.append('    (effects (font (size 1 1) (thickness 0.15)))')
    lines.append('  )')
    lines.append(
        f'  (fp_text value "{package}_{n_width}x{n_length}_'
        f'{ic_width_mm}x{ic_length_mm}mm_Pitch{pitch_mm}mm" '
        f'(at 0 {ic_length_mm/2 + 1}) (layer "F.Fab")'
    )
    lines.append('    (effects (font (size 1 1) (thickness 0.15)))')
    lines.append('  )')

    w2 = ic_width_mm / 2
    l2 = ic_length_mm / 2

    def emit_line(sx, sy, ex, ey, layer, width):
        lines.append(
            f'  (fp_line (start {sx} {sy}) (end {ex} {ey}) '
            f'(stroke (width {width}) (type solid)) (layer "{layer}"))'
        )

    # ── Top-left pin-1 orientation marker on silkscreen ──────────────────────
    emit_line(-w2 - 0.1, -l2 + 1.70, -w2 - 0.1, -l2 - 0.1, "F.SilkS", 0.12)
    emit_line(-w2 - 0.1, -l2 - 0.1, -w2 + 1.70, -l2 - 0.1, "F.SilkS", 0.12)

    # ── F.SilkS rectangle ────────────────────────────────────────────────────
    for sx, sy, ex, ey in [
        (w2,  -l2, -w2, -l2),
        (-w2, -l2, -w2,  l2),
        (-w2,  l2,  w2,  l2),
        (w2,   l2,  w2, -l2),
    ]:
        emit_line(sx, sy, ex, ey, "F.SilkS", 0.12)

    # ── F.Fab rectangle (slightly inset) + chamfered corner ──────────────────
    for sx, sy, ex, ey in [
        (w2 - 0.1,  -l2 + 0.1, -w2 + 0.1, -l2 + 0.1),
        (-w2 + 0.1, -l2 + 0.1, -w2 + 0.1,  l2 - 0.1),
        (-w2 + 0.1,  l2 - 0.1,  w2 - 0.1,  l2 - 0.1),
        (w2- 0.1,   l2 - 0.1,  w2 - 0.1, -l2 + 0.1),
        (-w2 + 0.1, -l2 + 0.5, -w2 + 0.5, -l2 + 0.1),  # chamfer pin-1 corner
    ]:
        emit_line(sx, sy, ex, ey, "F.Fab", 0.1)

    # ── F.CrtYd courtyard (0.7 mm beyond package outline) ────────────────────
    for sx, sy, ex, ey in [
        (w2 + 0.7,  -l2 - 0.7, -w2 - 0.7, -l2 - 0.7),
        (-w2 - 0.7, -l2 - 0.7, -w2 - 0.7,  l2 + 0.7),
        (-w2 - 0.7,  l2 + 0.7,  w2 + 0.7,  l2 + 0.7),
        (w2 + 0.7,   l2 + 0.7,  w2 + 0.7, -l2 - 0.7),
    ]:
        emit_line(sx, sy, ex, ey, "F.CrtYd", 0.05)

    # ── SMD pads ─────────────────────────────────────────────────────────────
    n_balls = 0
    for col_idx in range(n_width):
        for row_idx in range(n_length):
            if (col_idx, row_idx) in depopulate_set:
                continue
            pt_x = -calc_width / 2 + col_idx * pitch_mm
            pt_y = -calc_length / 2 + row_idx * pitch_mm
            pad_id = f"{COLUMN_LABELS[row_idx]}{ROW_LABELS[col_idx]}"
            lines.append(
                f'  (pad "{pad_id}" smd circle (at {pt_x} {pt_y}) '
                f'(size {ball_diam_mm} {ball_diam_mm}) '
                '(layers "F.Cu" "F.Paste" "F.Mask"))'
            )
            n_balls += 1

    # ── 3D model placeholder ─────────────────────────────────────────────────
    lines.append("  # 3D model — uncomment when a .wrl file is available.")
    lines.append(f"  # (model Housings_BGA.3dshapes/{name}.wrl")
    lines.append("  #   (at (xyz 0 0 0))")
    lines.append("  #   (scale (xyz 1 1 1))")
    lines.append("  #   (rotate (xyz 0 0 0))")
    lines.append("  # )")
    lines.append(")")

    # Append a one-line summary as a comment for human eyeballing
    summary = (
        f"# Generated by ukbfg_cli.py — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"# {name}\n"
        f"# {n_balls} pads, pitch {pitch_mm} mm, ball Ø {ball_diam_mm} mm\n"
    )
    return summary + "\n".join(lines) + "\n"


# ─── CLI ──────────────────────────────────────────────────────────────────────


def _parse_pair(text: str, sep: str = "x") -> tuple[float, float]:
    if sep not in text:
        raise argparse.ArgumentTypeError(f"Expected '{sep}'-separated pair, got '{text}'")
    parts = text.split(sep)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected two values separated by '{sep}', got '{text}'")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _parse_int_pair(text: str) -> tuple[int, int]:
    a, b = _parse_pair(text)
    return int(a), int(b)


def _parse_depopulate(text: str) -> list[tuple[int, int]]:
    """Parse '1,2;3,4;5,6' → [(1,2), (3,4), (5,6)]."""
    if not text:
        return []
    out: list[tuple[int, int]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            col, row = chunk.split(",")
            out.append((int(col), int(row)))
        except ValueError as e:
            raise argparse.ArgumentTypeError(
                f"Bad depopulate token '{chunk}' (expected 'col,row'): {e}") from e
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Generate a KiCAD BGA footprint (.kicad_mod) headlessly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--package",       required=True, help="Package name (e.g. PBGA256)")
    p.add_argument("--pins",          required=True, type=_parse_int_pair,
                   help="Pin grid as WxL, e.g. 16x16 (max 22x22)")
    p.add_argument("--pitch",         required=True, type=float, help="Ball pitch in mm")
    p.add_argument("--ball-diameter", required=True, type=float, help="Ball / pad diameter in mm")
    p.add_argument("--ic-size",       required=True, type=_parse_pair,
                   help="IC body size as WxL in mm, e.g. 27x27")
    p.add_argument("--depopulate",    type=_parse_depopulate, default=[],
                   help="Semicolon-separated col,row pairs to omit (e.g. '7,7;7,8;8,7;8,8')")
    p.add_argument("--output", "-o",  type=Path, default=None,
                   help="Output .kicad_mod path (default: <package>.kicad_mod)")

    args = p.parse_args()

    n_w, n_l = args.pins
    ic_w, ic_l = args.ic_size

    try:
        text = generate_kicad_mod(
            package=args.package,
            n_width=n_w,
            n_length=n_l,
            pitch_mm=args.pitch,
            ball_diam_mm=args.ball_diameter,
            ic_width_mm=ic_w,
            ic_length_mm=ic_l,
            depopulate=args.depopulate,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out = args.output or Path(f"{args.package}.kicad_mod")
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}  ({n_w}×{n_l} grid, {n_w*n_l - len(args.depopulate)} pads)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
