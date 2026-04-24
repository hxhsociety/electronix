# ElectroniX — Build Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Rust | ≥ 1.80 | https://rustup.rs |
| Node.js | ≥ 20 LTS | https://nodejs.org |
| npm | ≥ 10 | bundled with Node |

GPU compute (WGPU — used by rpim_pc and rpim_solver):
- Windows: DirectX 12 drivers (any modern GPU)
- Linux: Vulkan drivers (`apt install mesa-vulkan-drivers`)
- macOS: Metal (built-in)

---

## Quick start

### Windows
```powershell
# Debug build (fast compile, larger binary)
.\build.ps1

# Release build (optimised, ~3× faster solver)
.\build.ps1 -Release

# Then run:
.\_dist\run.ps1
```

### Linux / macOS
```bash
# Debug
make

# Release
make release

# Run (serve frontend + binaries from _dist/)
cd _dist && python3 -m http.server 5173
```

---

## Output layout

```
_dist/
├── gltf_convertor.exe   ← converts .cvg → model.glb
├── rpim_pc.exe          ← generates RPIM point cloud (.pcprep + thermal_summary.csv)
├── rpim_solver.exe      ← runs RPIM thermal + fatigue solver → solder_fatigue.json
├── run.ps1              ← Windows launcher (starts local server + opens browser)
└── public/              ← built frontend (index.html + assets/)
    ├── index.html
    ├── assets/
    ├── model.glb        ← (copied here by workflow after gltf_convertor runs)
    ├── thermal_summary.csv
    └── solder_fatigue.json
```

---

## Individual build steps

```powershell
# Rust only
.\build.ps1 -SkipFrontend

# Frontend only
.\build.ps1 -FrontendOnly

# Release Rust, skip frontend
.\build.ps1 -Release -SkipFrontend
```

---

## Workflow: full analysis run

```
1. Import PCB file
   gltf_convertor.exe  input.cvg  --output _dist/public/model.glb

2. Generate RPIM point cloud
   rpim_pc.exe  input.cvg  --output-dir <work_dir>
   → writes <work_dir>/thermal_summary.csv
   → writes <work_dir>/model.pcprep

3. Run thermal + fatigue solver
   rpim_solver.exe  <work_dir>/model.pcprep  --output-dir <work_dir>
   → writes <work_dir>/solder_fatigue.json

4. Copy results to frontend
   cp <work_dir>/thermal_summary.csv  _dist/public/
   cp <work_dir>/solder_fatigue.json  _dist/public/
```

---

## Development (hot-reload)

```bash
# Terminal 1 — backend binaries (rebuild on change)
cargo watch -x 'build --workspace'

# Terminal 2 — frontend dev server
cd frontend && npm run dev
# → http://localhost:5173
```

Install cargo-watch: `cargo install cargo-watch`

---

## Next: Tauri desktop app (planned)

The current setup runs as a local web application.  The next phase will wrap
this into a native desktop application using Tauri v2:

- Native file picker for Import
- Progress stream from solver to frontend
- Single installer (.msi / .dmg / .AppImage)
- No local server needed

See `src-tauri/` (to be created) for the Tauri backend.
