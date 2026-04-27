// ─── ElectroniX Tauri backend ────────────────────────────────────────────────
//
// Session layout  (%AppData%\ElectroniX\<uuid>\):
//   models\          ← <board>.glb  (gltf_convertor output)
//   point_cloud\     ← <board>.pcprep + rpim_pc.log
//   simulation\
//     <SimName>\     ← <SimName>.pcsim + solver outputs + rpim_solver.log
//
// Commands:
//   new_session            → create session dir, return session_id + base dirs
//   session_dirs           → resolve dirs for an existing session_id
//   pick_file              → native open-file dialog
//   run_import             → gltf_convertor → models/<board>.glb
//   run_generate_pc        → rpim_pc → point_cloud/<board>.pcprep
//   write_pcsim_file       → write simulation/<SimName>/<SimName>.pcsim
//   run_solver             → rpim_solver → simulation/<SimName>/…
//   run_solver_auto        → generate_pc + solver in one step
//   check_model_exists     → legacy check for frontend startup
//   read_json_file         → return file contents as string
//   read_trace_map         → parse *PCB_POINT_CLOUD from .pcprep
//   read_pcres             → parse binary .pcres result file

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};

// ─── Session management ───────────────────────────────────────────────────────

/// Root directory for all ElectroniX session data.
fn appdata_root() -> PathBuf {
    // Windows: %APPDATA%\ElectroniX
    // macOS:   ~/Library/Application Support/ElectroniX  (via HOME)
    // Linux:   ~/.local/share/ElectroniX
    let base = std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("."));
            if cfg!(target_os = "macos") {
                home.join("Library").join("Application Support")
            } else {
                home.join(".local").join("share")
            }
        });
    base.join("ElectroniX")
}

/// Paths for one session.
#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct SessionDirs {
    pub session_id:   String,
    pub root:         String,   // %AppData%/ElectroniX/<uuid>
    pub models_dir:   String,   // root/models
    pub pc_dir:       String,   // root/point_cloud
    pub sim_base_dir: String,   // root/simulation
}

impl SessionDirs {
    fn from_root(session_id: &str, root: &Path) -> Self {
        SessionDirs {
            session_id:   session_id.to_string(),
            root:         root.to_string_lossy().to_string(),
            models_dir:   root.join("models").to_string_lossy().to_string(),
            pc_dir:       root.join("point_cloud").to_string_lossy().to_string(),
            sim_base_dir: root.join("simulation").to_string_lossy().to_string(),
        }
    }

    fn create_dirs(&self) -> Result<(), String> {
        for d in [&self.models_dir, &self.pc_dir, &self.sim_base_dir] {
            std::fs::create_dir_all(d)
                .map_err(|e| format!("Cannot create {d}: {e}"))?;
        }
        Ok(())
    }
}

/// Create a fresh session folder and return its paths.
#[tauri::command]
async fn new_session() -> Result<SessionDirs, String> {
    let session_id = uuid_v4();
    let root = appdata_root().join(&session_id);
    let dirs = SessionDirs::from_root(&session_id, &root);
    dirs.create_dirs()?;
    Ok(dirs)
}

/// Resolve paths for an existing session_id (does NOT create dirs).
#[tauri::command]
async fn session_dirs(session_id: String) -> Result<SessionDirs, String> {
    let root = appdata_root().join(&session_id);
    if !root.exists() {
        return Err(format!("Session '{session_id}' not found"));
    }
    Ok(SessionDirs::from_root(&session_id, &root))
}

/// Tiny UUID v4 using rand (no extra dep — pulls from getrandom already in tree).
fn uuid_v4() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    // Deterministic-but-unique enough for local session IDs.
    // Uses process-mixed timestamp + counter; not crypto-grade.
    static COUNTER: std::sync::atomic::AtomicU64 =
        std::sync::atomic::AtomicU64::new(0);
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let cnt = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    // Mix ts + cnt into 128 bits via splitmix64
    let mut h = ts as u64 ^ 0x9e3779b97f4a7c15;
    h = h.wrapping_add(cnt).wrapping_mul(0x6c62272e07bb0142);
    h ^= h >> 30; h = h.wrapping_mul(0xbf58476d1ce4e5b9);
    h ^= h >> 27; h = h.wrapping_mul(0x94d049bb133111eb);
    let lo = h ^ (h >> 31);
    let hi = (ts >> 64) as u64 ^ cnt.wrapping_mul(0x517cc1b727220a95);
    format!("{:016x}-{:016x}", lo, hi)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/// Locate the project workspace root on disk.
fn workspace_root() -> Option<PathBuf> {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(|p| p.to_path_buf())
}

/// Locate a binary. Search order:
///   1. same dir as running exe (bundled / _dist layout)
///   2. <workspace>/target/release/
///   3. <workspace>/target/debug/
fn find_bin(name: &str) -> Option<PathBuf> {
    let exe_name = if cfg!(windows) { format!("{name}.exe") } else { name.to_string() };

    if let Ok(exe) = std::env::current_exe() {
        let c = exe.parent()?.join(&exe_name);
        if c.exists() { return Some(c); }
    }

    let ws = workspace_root()?;
    // Prefer debug over release so the latest rebuilt binary is always used in dev.
    // In production the exe-adjacent path (checked first above) takes precedence.
    let d = ws.join("target").join("debug").join(&exe_name);
    if d.exists() { return Some(d); }
    let r = ws.join("target").join("release").join(&exe_name);
    if r.exists() { return Some(r); }
    None
}

/// Progress event payload emitted as "job://progress"
#[derive(serde::Serialize, Clone)]
struct ProgressEvent {
    step:  String,
    line:  String,
    done:  bool,
    error: Option<String>,
}

fn emit(app: &AppHandle, step: &str, line: &str, done: bool, error: Option<String>) {
    let _ = app.emit("job://progress", ProgressEvent {
        step: step.to_string(), line: line.to_string(), done, error,
    });
}

/// Run a binary, stream every stdout line as a progress event, and write a log file.
fn run_streamed(
    app:      &AppHandle,
    step:     &str,
    bin:      &Path,
    args:     &[&str],
    log_path: Option<&Path>,
) -> Result<(), String> {
    let mut child = Command::new(bin)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start {}: {e}", bin.display()))?;

    let log_buf: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let log_buf2  = Arc::clone(&log_buf);
    let app2      = app.clone();
    let step_str  = step.to_string();
    let stdout    = child.stdout.take().unwrap();

    let handle = std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().flatten() {
            emit(&app2, &step_str, &line, false, None);
            log_buf2.lock().unwrap().push(line);
        }
    });

    let stderr_out = {
        let stderr = child.stderr.take().unwrap();
        let mut buf = String::new();
        for line in BufReader::new(stderr).lines().flatten() {
            buf.push_str(&line);
            buf.push('\n');
        }
        buf
    };

    let _ = handle.join();
    let status = child.wait().map_err(|e| e.to_string())?;

    if let Some(lp) = log_path {
        let lines = log_buf.lock().unwrap();
        let content = format!("{}\n{}", lines.join("\n"), stderr_out.trim());
        let _ = std::fs::write(lp, content);
    }

    if status.success() {
        emit(app, step, "Done.", true, None);
        Ok(())
    } else {
        let msg = format!("{step} failed (exit {:?})\n{}", status.code(), stderr_out.trim());
        emit(app, step, &msg, true, Some(msg.clone()));
        Err(msg)
    }
}

// ─── Commands ─────────────────────────────────────────────────────────────────

#[tauri::command]
async fn pick_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog().file()
        .add_filter("IPC-2581 board", &["cvg", "xml"])
        .pick_file(move |path| { let _ = tx.send(path.map(|p| p.to_string())); });
    rx.await.map_err(|e| e.to_string())
}

/// Check whether model.glb exists in the frontend public directory (legacy startup check).
#[tauri::command]
async fn check_model_exists() -> bool {
    workspace_root()
        .map(|w| w.join("frontend").join("public").join("model.glb").exists())
        .unwrap_or(false)
}

// ─── Import (CVG → GLB) ───────────────────────────────────────────────────────

/// Convert a CVG file to GLB.
///
/// Writes to `session/models/<board>.glb` (primary, session-managed copy) and
/// also copies to `<workspace>/frontend/public/model.glb` so the Viewer3D
/// dev-server path `/model.glb` continues to work during development.
/// Returns the session GLB path.
#[tauri::command]
async fn run_import(
    app:        AppHandle,
    cvg_path:   String,
    session_id: String,
) -> Result<String, String> {
    let models_dir = appdata_root().join(&session_id).join("models");
    std::fs::create_dir_all(&models_dir)
        .map_err(|e| format!("Cannot create models dir: {e}"))?;

    let board_stem = board_stem(&cvg_path);
    let glb_path   = models_dir.join(format!("{board_stem}.glb"));
    let glb_str    = glb_path.to_string_lossy().to_string();

    emit(&app, "import", "Converting CVG → GLB…", false, None);

    let bin = find_bin("gltf_convertor")
        .ok_or_else(|| "gltf_convertor binary not found".to_string())?;

    run_streamed(&app, "import", &bin, &[&cvg_path, "--glb", &glb_str], None)?;

    // Dev-mode compat: Viewer3D loads from `/model.glb` served by the Vite dev
    // server out of frontend/public/.  Copy the generated file there so the 3D
    // viewer updates without touching the Viewer3D component.
    if let Some(ws) = workspace_root() {
        let pub_glb = ws.join("frontend").join("public").join("model.glb");
        if let Some(parent) = pub_glb.parent() { std::fs::create_dir_all(parent).ok(); }
        std::fs::copy(&glb_path, &pub_glb).ok();
    }

    Ok(glb_str)
}

// ─── Point cloud generation ───────────────────────────────────────────────────

/// Result of run_generate_pc.
#[derive(serde::Serialize, Clone)]
struct GeneratePcResult {
    pcprep_path:    String,
    config_path:    String,
    materials_path: String,
}

/// Run rpim_pc and write outputs to `session/point_cloud/`.
///
/// config_json — serialised PointCloudSettings JSON (empty string = use defaults)
/// Returns paths to the generated .pcprep / config / materials files.
#[tauri::command]
async fn run_generate_pc(
    app:        AppHandle,
    cvg_path:   String,
    config_json: String,
    session_id: String,
) -> Result<GeneratePcResult, String> {
    let pc_dir = appdata_root().join(&session_id).join("point_cloud");
    std::fs::create_dir_all(&pc_dir)
        .map_err(|e| format!("Cannot create point_cloud dir: {e}"))?;

    let board_stem   = board_stem(&cvg_path);
    let out_base     = pc_dir.join(&board_stem);
    let out_base_str = out_base.to_string_lossy().to_string();

    let bin = find_bin("rpim_pc")
        .ok_or_else(|| "rpim_pc binary not found".to_string())?;

    let log_path = pc_dir.join("rpim_pc.log");

    emit(&app, "generate_pc", "Generating RPIM point cloud…", false, None);

    if config_json.trim().is_empty() {
        // No config — let rpim_pc use its defaults
        run_streamed(
            &app, "generate_pc", &bin,
            &[&cvg_path, "--out", &out_base_str],
            Some(&log_path),
        )?;
    } else {
        // Write the config JSON next to the pcprep output
        let json_path    = pc_dir.join(format!("{board_stem}_rpim_input.json"));
        let json_path_str = json_path.to_string_lossy().to_string();
        std::fs::write(&json_path, &config_json)
            .map_err(|e| format!("Cannot write rpim_input.json: {e}"))?;
        run_streamed(
            &app, "generate_pc", &bin,
            &[&cvg_path, &json_path_str, "--out", &out_base_str],
            Some(&log_path),
        )?;
    }

    Ok(GeneratePcResult {
        pcprep_path:    format!("{out_base_str}.pcprep"),
        config_path:    format!("{out_base_str}_rpim_config.json"),
        materials_path: format!("{out_base_str}_rpim_materials.json"),
    })
}

// ─── .pcsim writer ────────────────────────────────────────────────────────────

/// Parameters for a .pcsim simulation load deck.
#[derive(serde::Deserialize)]
struct PcsimParams {
    session_id:  String,
    sim_name:    String,
    pcprep_path: String,   // absolute path to the .pcprep (will be made relative)
    ambient_c:   f64,
    curve_name:  String,
    profile_pts: Vec<[f64; 2]>,   // [[time_min, temp_c], ...]
    n_cycles:    usize,
    pad_d_mm:    f64,
    solder_h_mm: f64,
}

/// Write a .pcsim deck to `session/simulation/<sim_name>/<sim_name>.pcsim`.
///
/// The *INCLUDE path inside the file is written relative to the .pcsim location
/// so the file is portable if the session folder is moved.
/// Returns the absolute path of the written .pcsim.
#[tauri::command]
async fn write_pcsim_file(params: PcsimParams) -> Result<String, String> {
    let sim_dir = appdata_root()
        .join(&params.session_id)
        .join("simulation")
        .join(&params.sim_name);
    std::fs::create_dir_all(&sim_dir)
        .map_err(|e| format!("Cannot create simulation dir: {e}"))?;

    let pcsim_path = sim_dir.join(format!("{}.pcsim", params.sim_name));

    // Make the pcprep path relative to the .pcsim directory so the file is portable.
    let pcprep_rel = relative_path(&sim_dir, Path::new(&params.pcprep_path));

    let pts: Vec<(f64, f64)> = params.profile_pts.iter().map(|p| (p[0], p[1])).collect();
    let text = build_pcsim_text(
        &pcprep_rel, params.ambient_c, &params.curve_name,
        &pts, params.n_cycles, params.pad_d_mm, params.solder_h_mm,
    );

    std::fs::write(&pcsim_path, &text)
        .map_err(|e| format!("Cannot write .pcsim: {e}"))?;

    Ok(pcsim_path.to_string_lossy().to_string())
}

fn build_pcsim_text(
    pcprep_path: &str, ambient_c: f64, curve_name: &str,
    profile_pts: &[(f64, f64)], n_cycles: usize, pad_d_mm: f64, solder_h_mm: f64,
) -> String {
    let mut s = String::with_capacity(512);
    s.push_str("# ElectroniX PCSIM — Simulation Load Deck\n");
    s.push_str("# Generated by ElectroniX Reliability Workbench\n#\n");
    s.push_str(&format!("*INCLUDE, PCPREP=\"{pcprep_path}\"\n\n"));
    s.push_str(&format!("*AMBIENT_TEMPERATURE, UNIT=CELSIUS\n{ambient_c}\n\n"));
    s.push_str(&format!("*CURVE, NAME=\"{curve_name}\", X=Time_min, Y=Temperature_C\n"));
    for (t, v) in profile_pts { s.push_str(&format!("{t:.4}, {v:.4}\n")); }
    s.push('\n');
    s.push_str(&format!("*CYCLES\n{n_cycles}\n\n"));
    s.push_str(&format!("*PAD_DIAMETER\n{pad_d_mm}\n\n"));
    s.push_str(&format!("*SOLDER_HEIGHT\n{solder_h_mm}\n\n"));
    s.push_str(&format!("*CREEP, CURVE=\"{curve_name}\"\n\n"));
    s.push_str("*END\n");
    s
}

/// Compute a relative path from `base_dir` to `target`.
/// Falls back to the absolute path string if relative can't be determined.
fn relative_path(base_dir: &Path, target: &Path) -> String {
    // Walk up from base_dir until we find a common prefix, then build ../.. chain
    let base_parts: Vec<_>   = base_dir.components().collect();
    let target_parts: Vec<_> = target.components().collect();
    let common = base_parts.iter().zip(target_parts.iter())
        .take_while(|(a, b)| a == b).count();
    if common == 0 {
        return target.to_string_lossy().to_string();
    }
    let ups = base_parts.len() - common;
    let mut rel = PathBuf::new();
    for _ in 0..ups { rel.push(".."); }
    for part in &target_parts[common..] { rel.push(part); }
    // Use forward slashes (works on all platforms in pcsim parser)
    rel.to_string_lossy().replace('\\', "/")
}

// ─── Solver ───────────────────────────────────────────────────────────────────

/// Run rpim_solver on a .pcsim file inside `session/simulation/<sim_name>/`.
///
/// Outputs land in the same simulation folder; log written to rpim_solver.log.
/// Returns path to the solder_fatigue.json result.
#[tauri::command]
async fn run_solver(
    app:        AppHandle,
    pcsim_path: String,
    session_id: String,
    sim_name:   String,
) -> Result<String, String> {
    let sim_dir = appdata_root()
        .join(&session_id)
        .join("simulation")
        .join(&sim_name);
    std::fs::create_dir_all(&sim_dir)
        .map_err(|e| format!("Cannot create simulation dir: {e}"))?;

    let sim_dir_str = sim_dir.to_string_lossy().to_string();
    let log_path    = sim_dir.join("rpim_solver.log");

    emit(&app, "solver", "Running RPIM creep + fatigue solver…", false, None);

    let bin = find_bin("rpim_solver")
        .ok_or_else(|| "rpim_solver binary not found".to_string())?;

    run_streamed(
        &app, "solver", &bin,
        &[&pcsim_path, "--out-dir", &sim_dir_str],
        Some(&log_path),
    )?;

    // Find the fatigue JSON — solver names it <stem>_solder_fatigue.json
    let stem = Path::new(&pcsim_path)
        .file_stem().unwrap_or_default()
        .to_string_lossy().to_string();
    Ok(format!("{sim_dir_str}/{stem}_solder_fatigue.json"))
}

/// Result returned by run_solver_auto.
#[derive(serde::Serialize, Clone)]
struct SolverAutoResult {
    fatigue_path: String,
    pcprep_path:  String,
}

/// Auto-generate pcprep with defaults + run solver; used for "quick solve" flow.
#[tauri::command]
async fn run_solver_auto(
    app:        AppHandle,
    cvg_path:   String,
    session_id: String,
    sim_name:   String,
) -> Result<SolverAutoResult, String> {
    // ── Step 1: point cloud ───────────────────────────────────────────────────
    let pc_dir = appdata_root().join(&session_id).join("point_cloud");
    std::fs::create_dir_all(&pc_dir)
        .map_err(|e| format!("Cannot create point_cloud dir: {e}"))?;

    let board_stem   = board_stem(&cvg_path);
    let out_base     = pc_dir.join(&board_stem);
    let out_base_str = out_base.to_string_lossy().to_string();
    let pc_log       = pc_dir.join("rpim_pc.log");

    let pc_bin = find_bin("rpim_pc")
        .ok_or_else(|| "rpim_pc binary not found".to_string())?;

    emit(&app, "generate_pc", "Auto-generating RPIM point cloud…", false, None);
    run_streamed(
        &app, "generate_pc", &pc_bin,
        &[&cvg_path, "--out", &out_base_str],
        Some(&pc_log),
    )?;

    let pcprep_path = format!("{out_base_str}.pcprep");
    if !Path::new(&pcprep_path).exists() {
        return Err(format!("rpim_pc did not produce {pcprep_path}"));
    }

    // ── Step 2: write default pcsim ───────────────────────────────────────────
    let sim_dir = appdata_root()
        .join(&session_id).join("simulation").join(&sim_name);
    std::fs::create_dir_all(&sim_dir)
        .map_err(|e| format!("Cannot create simulation dir: {e}"))?;

    let pcsim_path = sim_dir.join(format!("{sim_name}.pcsim"));
    let pcprep_rel = relative_path(&sim_dir, Path::new(&pcprep_path));
    // TC-B default profile: -55 → +125°C
    let default_pts = vec![
        (0.0, 25.0), (6.0, -55.0), (21.0, -55.0),
        (34.0, 125.0), (49.0, 125.0), (56.0, 25.0),
    ];
    let pcsim_text = build_pcsim_text(
        &pcprep_rel, 25.0, "TC-B", &default_pts, 1, 0.5, 0.30,
    );
    std::fs::write(&pcsim_path, &pcsim_text)
        .map_err(|e| format!("Cannot write auto .pcsim: {e}"))?;

    // ── Step 3: solve ─────────────────────────────────────────────────────────
    let sim_dir_str  = sim_dir.to_string_lossy().to_string();
    let pcsim_str    = pcsim_path.to_string_lossy().to_string();
    let solver_log   = sim_dir.join("rpim_solver.log");

    let solver_bin = find_bin("rpim_solver")
        .ok_or_else(|| "rpim_solver binary not found".to_string())?;

    emit(&app, "solver", "Running RPIM creep + fatigue solver…", false, None);
    run_streamed(
        &app, "solver", &solver_bin,
        &[&pcsim_str, "--out-dir", &sim_dir_str],
        Some(&solver_log),
    )?;

    Ok(SolverAutoResult {
        fatigue_path: format!("{sim_dir_str}/{sim_name}_solder_fatigue.json"),
        pcprep_path:  pcprep_path,
    })
}

// ─── File utilities ───────────────────────────────────────────────────────────

/// Read any text/JSON file from disk and return its contents as a string.
#[tauri::command]
async fn read_json_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path)
        .map_err(|e| format!("Cannot read {path}: {e}"))
}

/// One node from the *PCB_POINT_CLOUD section of a .pcprep file.
#[derive(serde::Serialize, Clone)]
struct TraceNode { x: f32, y: f32, z: f32, metal_fraction: f32 }

/// Parse the *PCB_POINT_CLOUD section from a .pcprep file (trace-map view).
#[tauri::command]
async fn read_trace_map(pcprep_path: String) -> Result<Vec<TraceNode>, String> {
    let content = std::fs::read_to_string(&pcprep_path)
        .map_err(|e| format!("Cannot read {pcprep_path}: {e}"))?;

    let mut nodes: Vec<TraceNode> = Vec::new();
    let mut in_block = false;

    for line in content.lines() {
        let t = line.trim();
        if t.to_ascii_uppercase().starts_with("*PCB_POINT_CLOUD") {
            in_block = true; continue;
        }
        if in_block {
            if t.starts_with('*') { break; }
            if t.is_empty() || t.starts_with('#') || t.starts_with("//") { continue; }
            let p: Vec<&str> = t.split_whitespace().collect();
            if p.len() >= 4 {
                if let (Ok(x), Ok(y), Ok(z), Ok(mf)) = (
                    p[0].parse::<f32>(), p[1].parse::<f32>(),
                    p[2].parse::<f32>(), p[3].parse::<f32>(),
                ) { nodes.push(TraceNode { x, y, z, metal_fraction: mf }); }
            }
        }
    }
    Ok(nodes)
}

// ─── .pcres reader ────────────────────────────────────────────────────────────

const PCRES_MAGIC:   &[u8; 8] = b"PCRESV1\n";
const PCRES_CREEP:   u8 = 0;
const PCRES_THERMAL: u8 = 1;
const FTYPE_F32:     u8 = 0;
const FTYPE_U32:     u8 = 1;
const FTYPE_STR:     u8 = 2;

#[derive(serde::Serialize, Clone)]
#[serde(tag = "type")]
enum PcresRecord {
    Creep {
        node_id: u32, body_name: String,
        x: f32, y: f32, z: f32,
        ux_um: f32, uy_um: f32, uz_um: f32, mag_um: f32, dw_mpa: f32,
    },
    Thermal {
        node_id: u32, body_name: String, face_tag: String,
        x: f32, y: f32, z: f32,
        t_min_c: f32, t_max_c: f32, delta_t_c: f32, material_id: u32,
    },
}

/// Parse a `.pcres` file and return only solder-joint creep records.
///
/// The full creep pcres contains all assembly nodes (PCB + components + solder joints).
/// Solder joints are a small fraction; this avoids transferring millions of records
/// over IPC when only the ΔW / displacement contour on solder joints is needed.
#[tauri::command]
async fn read_pcres_solder(path: String) -> Result<Vec<PcresRecord>, String> {
    let all = read_pcres_data(&path)?;
    Ok(all.into_iter().filter(|r| {
        if let PcresRecord::Creep { body_name, .. } = r { body_name.starts_with("solder_") }
        else { false }
    }).collect())
}

/// Parse a `.pcres` binary file and return all records as JSON-serialisable values.
#[tauri::command]
async fn read_pcres(path: String) -> Result<Vec<PcresRecord>, String> {
    read_pcres_data(&path)
}

fn read_pcres_data(path: &str) -> Result<Vec<PcresRecord>, String> {
    let data = std::fs::read(path)
        .map_err(|e| format!("Cannot read '{path}': {e}"))?;

    let mut pos = 0usize;

    macro_rules! read_u8  { () => {{ let v = data[pos]; pos += 1; v }} }
    macro_rules! read_u16 { () => {{ let v = u16::from_le_bytes(data[pos..pos+2].try_into().unwrap()); pos += 2; v }} }
    macro_rules! read_u32 { () => {{ let v = u32::from_le_bytes(data[pos..pos+4].try_into().unwrap()); pos += 4; v }} }
    macro_rules! read_f32 { () => {{ let v = f32::from_le_bytes(data[pos..pos+4].try_into().unwrap()); pos += 4; v }} }

    if data.get(..8) != Some(PCRES_MAGIC.as_slice()) {
        return Err("Not a valid PCRES file (bad magic)".into());
    }
    pos = 8;

    let result_type = read_u8!();
    let field_count = read_u8!() as usize;
    let n           = read_u32!() as usize;

    let mut field_types: Vec<u8>      = Vec::with_capacity(field_count);
    let mut _field_names: Vec<String> = Vec::with_capacity(field_count);
    for _ in 0..field_count {
        let ftype    = read_u8!();
        let name_len = read_u8!() as usize;
        let name = std::str::from_utf8(&data[pos..pos + name_len])
            .unwrap_or("?").to_string();
        pos += name_len;
        field_types.push(ftype);
        _field_names.push(name);
    }

    let pool_size  = read_u32!() as usize;
    let pool_start = pos;
    pos += pool_size;

    let read_pool_str = |off: usize| -> String {
        let start = pool_start + off;
        let end   = data[start..].iter().position(|&b| b == 0)
            .map(|i| start + i).unwrap_or(pool_start + pool_size);
        String::from_utf8_lossy(&data[start..end]).to_string()
    };

    let mut records = Vec::with_capacity(n);
    for _ in 0..n {
        let x       = read_f32!();
        let y       = read_f32!();
        let z       = read_f32!();
        let node_id = read_u32!();

        let mut fvals: Vec<f32>    = Vec::new();
        let mut uvals: Vec<u32>    = Vec::new();
        let mut svals: Vec<String> = Vec::new();

        for &ft in &field_types {
            match ft {
                FTYPE_F32 => { fvals.push(read_f32!()); }
                FTYPE_U32 => { uvals.push(read_u32!()); }
                FTYPE_STR => { svals.push(read_pool_str(read_u16!() as usize)); }
                _ => { pos += 4; }
            }
        }

        let rec = match result_type {
            PCRES_CREEP => PcresRecord::Creep {
                node_id,
                body_name: svals.into_iter().next().unwrap_or_default(),
                x, y, z,
                ux_um:  fvals.first().copied().unwrap_or(0.0),
                uy_um:  fvals.get(1).copied().unwrap_or(0.0),
                uz_um:  fvals.get(2).copied().unwrap_or(0.0),
                mag_um: fvals.get(3).copied().unwrap_or(0.0),
                dw_mpa: fvals.get(4).copied().unwrap_or(0.0),
            },
            PCRES_THERMAL => {
                let mut si = svals.into_iter();
                PcresRecord::Thermal {
                    node_id,
                    body_name:   si.next().unwrap_or_default(),
                    face_tag:    si.next().unwrap_or_default(),
                    x, y, z,
                    t_min_c:    fvals.first().copied().unwrap_or(0.0),
                    t_max_c:    fvals.get(1).copied().unwrap_or(0.0),
                    delta_t_c:  fvals.get(2).copied().unwrap_or(0.0),
                    material_id: uvals.into_iter().next().unwrap_or(0),
                }
            }
            _ => continue,
        };
        records.push(rec);
    }
    Ok(records)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/// Extract the file stem from a CVG path for use as the board base name.
fn board_stem(cvg_path: &str) -> String {
    Path::new(cvg_path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string()
}

// ─── App entry point ──────────────────────────────────────────────────────────

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            new_session,
            session_dirs,
            pick_file,
            check_model_exists,
            run_import,
            run_generate_pc,
            write_pcsim_file,
            run_solver,
            run_solver_auto,
            read_json_file,
            read_trace_map,
            read_pcres,
            read_pcres_solder,
        ])
        .run(tauri::generate_context!())
        .expect("error while running ElectroniX");
}
