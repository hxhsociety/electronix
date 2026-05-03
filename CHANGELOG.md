# Changelog

All notable changes to ElectroniX Reliability Workbench are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versioning: [Semantic Versioning](https://semver.org/)

---

## [Unreleased]

---

## [0.1.0] — 2026-05-03

### Added

#### Solver
- **Anand viscoplastic creep model** — full ODE integration for solder joint creep strain energy density (ΔW per cycle).
- **Darveaux energy-based fatigue** — `N₀ = K₁·ΔW^K₂` (crack initiation) + `da/dN = K₃·ΔW^K₄` (crack growth) → characteristic life Nf.
- **Multi-alloy support** — SAC305, SAC405, SnPb37 with literature constants (Darveaux 1994, Clech 2004, Syed 2004, Pao 1992).
- **Adaptive Runge-Kutta integration** — NaN/inf guards; automatic sub-stepping to prevent divergence.
- **`.pcsim` deck format** — keyword-driven load deck (`*INCLUDE`, `*CURVE`, `*CYCLES`, `*CREEP`, `*SOLDER_ALLOY`, `*MATERIAL_OVERRIDE`, `*END`).
- **`.pcres` binary result format** — per-joint Nf, ΔW, strain range; streamed live during solve.
- **Session file architecture** — all intermediate files written to `%AppData%\ElectroniX\<SessionID>\`.

#### Thermal cycling profiles
- **JEDEC JESD22-A104** — conditions A through H (−55/+85 °C through 0/+100 °C).
- **IPC-9701A** — TC1–TC4 consumer/automotive/harsh profiles.
- **AEC-Q100** — Grades 0–3 engine compartment through interior.
- **MIL-STD-810H Method 503** — Procedures I, II, III.
- **ECSS/Space** — TC1, TC2, LEO orbit (−40/+85 °C, 90 min period).
- **IEC 60068-2-14** — Test Na and Nb (rapid/slow thermal change).
- **Custom curves** — arbitrary time-temperature tables via `*CURVE`.

#### UI
- **Board import** — STEP/IDF → glTF via `gltf_convertor`; 3D viewer with body/face tagging.
- **Point cloud generator** — `rpim_pc` integration point tagging per body.
- **Simulation browser** — create/manage simulation cases; solder alloy picker; pad geometry inputs.
- **Material library** — 30+ materials (FR4, copper, SAC305, SAC405, SnPb37, aluminium, steel, …).
- **Material property editor** — per-body Anand, Darveaux, CTE, Young's modulus, thermal conductivity overrides.
- **Live solver log** — streaming output from `rpim_solver` in the UI.
- **Results viewer** — per-joint Nf / ΔW scatter and table; colour-coded by fatigue life.

### Technical notes
- Built with Tauri 2 (Rust backend) + React 18 + TypeScript.
- Rust solver (`rpim_solver`) is a standalone CLI — can be run headless.
- All solver constants are traceable to peer-reviewed literature; see `rpim_solver/REFERENCES.md`.

---

[Unreleased]: https://github.com/Solaris/ElectroniX/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Solaris/ElectroniX/releases/tag/v0.1.0
