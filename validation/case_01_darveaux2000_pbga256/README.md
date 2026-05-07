# Case 01 — Darveaux 2000 PBGA 256

ElectroniX solder fatigue validation against:

> Darveaux, R. (2000). *Effect of Rate of Change of Temperature on Solder Joint Fatigue.*
> Proc. ASME InterPACK 2000.

**Expected Nf:** 6,500 cycles (η, β=4.5) under JEDEC TC1 (0/+100 °C, 2 cph).
**Pass band:** 5,200 ≤ Nf ≤ 7,800 (±20% per IPC-9701A).

---

## Geometry summary

| Parameter | Value |
|---|---|
| Package body | 27 × 27 mm |
| Ball pitch | 1.0 mm |
| Ball count | 256 (16 × 16 grid) |
| Ball diameter | 0.60 mm (equator) |
| Solder standoff | 0.50 mm |
| Pad diameter | 0.55 mm |
| BT substrate | 0.36 mm |
| Die | 10 × 10 × 0.30 mm |
| Mold cap | 25 × 25 × 1.10 mm |
| PCB coupon | 50 × 50 × 1.60 mm (FR4, 4-layer) |

---

## Workflow: OpenSCAD → ElectroniX

### 1. Generate STLs (one per body)

```powershell
.\build_stls.ps1
```

Produces 7 files in `stl/`:
- `pcb.stl` (FR4) — material id 1
- `pads.stl` (Cu) — id 2
- `balls.stl` (SAC305, 256 joints) — id 4
- `substrate.stl` (BT) — id 9
- `die_attach.stl` (epoxy) — id 11
- `die.stl` (Si) — id 10
- `mold.stl` (EMC) — id 8

### 2. Assemble into a STEP file with named parts

OpenSCAD only exports STL (no part names). To preserve the body names that
ElectroniX needs in the `.pcprep`, use **FreeCAD** to combine them:

```
1. Open FreeCAD → File → New
2. Mesh → Import mesh → select all 7 STLs
3. For each mesh:
     - Right-click → Rename → use the body name (pcb, pads, balls, ...)
     - Mesh → Convert to shape (refine + sew)
     - Part → Convert solid
4. Select all 7 solids → Part → Compound → Make compound
5. File → Export → STEP (with attributes) → board.step
```

Or use the supplied FreeCAD macro:

```powershell
freecad --console -- "..\..\scripts\stl_to_step.py" stl board.step
```

### 3. Import into ElectroniX

```
File → Import → board.step
```

ElectroniX will:
1. Run `gltf_convertor` → `models/board.glb` (preserves body names)
2. Tag bodies in the 3D viewer (assign material IDs in the UI)
3. Run `rpim_pc` → `point_cloud/board.pcprep`

### 4. Run the validation simulation

The matching `.pcsim` deck is in `case.pcsim`:
- `*INCLUDE` → the generated `.pcprep`
- `*CURVE TC1` → 0/+100 °C, 15 min ramp, 15 min dwell
- `*CYCLES` → 8000 (run past expected failure)
- `*SOLDER_ALLOY SAC305`

Click **Run** in the Simulation Browser and wait for the solve to finish.

### 5. Compare against `expected.yaml`

The result PCRES + fatigue JSON should report Nf within `[5200, 7800]`.

---

## Files in this directory

| File | Purpose |
|---|---|
| `board.scad` | Parametric OpenSCAD source |
| `build_stls.ps1` | Render all 7 bodies to separate STLs |
| `expected.yaml` | Reference Nf, tolerance, paper citation |
| `case.pcsim` | (To be added) load deck for the JEDEC TC1 cycle |
| `README.md` | This file |
