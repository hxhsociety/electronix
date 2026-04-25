// ─── ElectroniX Tauri backend ────────────────────────────────────────────────
//
// Commands exposed to the frontend:
//   pick_file          → native open-file dialog → returns chosen path
//   run_import         → gltf_convertor.exe cvg → model.glb
//   run_generate_pc    → writes rpim_input.json  → rpim_pc.exe
//   run_solver         → rpim_solver.exe
//
// All long-running commands stream stdout lines as "job://progress" events so
// the React status pill can show live output without blocking.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};

// ─── helpers ─────────────────────────────────────────────────────────────────

/// Locate the project workspace root on disk.
fn workspace_root() -> Option<PathBuf> {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(|p| p.to_path_buf())
}

/// Locate a binary.  Search order:
///   1. same directory as the running exe  (bundled / _dist layout)
///   2. <workspace>/target/release/        (release dev)
///   3. <workspace>/target/debug/          (debug dev)
fn find_bin(name: &str) -> Option<PathBuf> {
    let exe_name = if cfg!(windows) {
        format!("{}.exe", name)
    } else {
        name.to_string()
    };

    // 1. next to current exe
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent()?.join(&exe_name);
        if candidate.exists() {
            return Some(candidate);
        }
    }

    let workspace = workspace_root()?;

    // 2. release build
    let release = workspace.join("target").join("release").join(&exe_name);
    if release.exists() {
        return Some(release);
    }

    // 3. debug build
    let debug = workspace.join("target").join("debug").join(&exe_name);
    if debug.exists() {
        return Some(debug);
    }

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
    let _ = app.emit(
        "job://progress",
        ProgressEvent {
            step:  step.to_string(),
            line:  line.to_string(),
            done,
            error,
        },
    );
}

/// Run a binary, stream every stdout line as a progress event, and optionally
/// write the full transcript to `log_path` (e.g. rpim_pc.dat).
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
        .map_err(|e| format!("Failed to start {}: {}", bin.display(), e))?;

    // Shared buffer: stdout lines collected for the log AND emitted as events
    let log_buf: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let log_buf_clone = Arc::clone(&log_buf);
    let app_clone     = app.clone();
    let step_str      = step.to_string();
    let stdout        = child.stdout.take().unwrap();

    let handle = std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().flatten() {
            emit(&app_clone, &step_str, &line, false, None);
            log_buf_clone.lock().unwrap().push(line);
        }
    });

    // Collect stderr for error reporting
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

    // Write log file
    if let Some(lp) = log_path {
        let lines = log_buf.lock().unwrap();
        let content = format!("{}\n{}", lines.join("\n"), stderr_out.trim());
        let _ = std::fs::write(lp, content);
    }

    if status.success() {
        emit(app, step, "Done.", true, None);
        Ok(())
    } else {
        let msg = format!("{} failed (exit {:?})\n{}", step, status.code(), stderr_out.trim());
        emit(app, step, &msg, true, Some(msg.clone()));
        Err(msg)
    }
}

// ─── commands ────────────────────────────────────────────────────────────────

/// Show a native open-file dialog filtered to .cvg / .xml.
/// Returns the selected path, or None if cancelled.
///
/// blocking_pick_file() dispatches to the UI main thread internally, so it
/// must run on its own OS thread (not an async executor thread).
/// spawn_blocking() provides that dedicated thread.
#[tauri::command]
async fn pick_file(app: AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;

    let path = tokio::task::spawn_blocking(move || {
        app.dialog()
            .file()
            .add_filter("IPC-2581 board", &["cvg", "xml"])
            .blocking_pick_file()
    })
    .await
    .map_err(|e| e.to_string())?;

    Ok(path.map(|p| p.to_string()))
}

/// Check whether model.glb exists in the frontend public directory.
/// Called by App.tsx on startup to decide whether to show the project
/// screen or the main viewer — more reliable than a fetch() in WebView2.
#[tauri::command]
async fn check_model_exists() -> bool {
    workspace_root()
        .map(|w| w.join("frontend").join("public").join("model.glb").exists())
        .unwrap_or(false)
}

/// Convert a CVG board file to GLB using gltf_convertor.
///
/// out_dir  — directory to place model.glb  (created if absent)
/// Returns the path of the written GLB.
#[tauri::command]
async fn run_import(
    app: AppHandle,
    cvg_path: String,
    out_dir: Option<String>,
) -> Result<String, String> {
    let workspace = workspace_root().ok_or_else(|| "Workspace root could not be determined".to_string())?;
    let out_dir = out_dir.unwrap_or_else(|| workspace.join("frontend").join("public").to_string_lossy().to_string());

    std::fs::create_dir_all(&out_dir)
        .map_err(|e| format!("Cannot create out_dir: {e}"))?;

    let glb_path = Path::new(&out_dir).join("model.glb");
    let glb_str = glb_path.to_string_lossy().to_string();

    emit(&app, "import", "Converting CVG → GLB…", false, None);

    let bin = find_bin("gltf_convertor")
        .ok_or_else(|| "gltf_convertor binary not found".to_string())?;

    run_streamed(&app, "import", &bin, &[&cvg_path, "--glb", &glb_str], None)?;

    Ok(glb_str)
}

/// Result of run_generate_pc — paths to all output files.
#[derive(serde::Serialize, Clone)]
struct GeneratePcResult {
    pcprep_path:    String,
    config_path:    String,
    materials_path: String,
}

/// Write rpim_input.json to disk then run rpim_pc.
///
/// config_json — serialised PointCloudSettings JSON string
/// out_dir     — directory for all rpim_pc outputs  (created if absent)
/// Returns paths to the generated pcprep / config / materials files.
#[tauri::command]
async fn run_generate_pc(
    app: AppHandle,
    cvg_path: String,
    config_json: String,
    out_dir: String,
) -> Result<GeneratePcResult, String> {
    std::fs::create_dir_all(&out_dir)
        .map_err(|e| format!("Cannot create out_dir: {e}"))?;

    // derive base name from cvg filename
    let base = Path::new(&cvg_path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string();

    // write the config
    let json_path = Path::new(&out_dir).join(format!("{base}_rpim_input.json"));
    std::fs::write(&json_path, &config_json)
        .map_err(|e| format!("Cannot write rpim_input.json: {e}"))?;

    let out_base     = Path::new(&out_dir).join(&base);
    let out_base_str = out_base.to_string_lossy().to_string();
    let json_str     = json_path.to_string_lossy().to_string();

    emit(&app, "generate_pc", "Generating RPIM point cloud…", false, None);

    let bin = find_bin("rpim_pc")
        .ok_or_else(|| "rpim_pc binary not found".to_string())?;

    let log_path = Path::new(&out_dir).join("rpim_pc.dat");
    run_streamed(
        &app,
        "generate_pc",
        &bin,
        &[&cvg_path, &json_str, "--out", &out_base_str],
        Some(&log_path),
    )?;

    Ok(GeneratePcResult {
        pcprep_path:    format!("{out_base_str}.pcprep"),
        config_path:    format!("{out_base_str}_rpim_config.json"),
        materials_path: format!("{out_base_str}_rpim_materials.json"),
    })
}

/// Read any text/JSON file from disk and return its contents as a string.
/// Used by the frontend to load rpim_config.json after point-cloud generation.
#[tauri::command]
async fn read_json_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path)
        .map_err(|e| format!("Cannot read {path}: {e}"))
}

/// One node from the *PCB_POINT_CLOUD section of a .pcprep file.
#[derive(serde::Serialize, Clone)]
struct TraceNode {
    x:              f32,
    y:              f32,
    z:              f32,
    metal_fraction: f32,
}

/// Parse the *PCB_POINT_CLOUD section from a .pcprep file.
/// Returns every line that has four whitespace-separated numbers.
/// Used by the Material Browser's "Trace Map" view.
#[tauri::command]
async fn read_trace_map(pcprep_path: String) -> Result<Vec<TraceNode>, String> {
    let content = std::fs::read_to_string(&pcprep_path)
        .map_err(|e| format!("Cannot read {pcprep_path}: {e}"))?;

    let mut nodes: Vec<TraceNode> = Vec::new();
    let mut in_block = false;

    for line in content.lines() {
        let trimmed = line.trim();

        if trimmed.eq_ignore_ascii_case("*PCB_POINT_CLOUD")
            || trimmed.to_ascii_uppercase().starts_with("*PCB_POINT_CLOUD")
        {
            in_block = true;
            continue;
        }

        if in_block {
            // A new section header ends the block
            if trimmed.starts_with('*') {
                break;
            }
            if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with("//") {
                continue;
            }
            let parts: Vec<&str> = trimmed.split_whitespace().collect();
            if parts.len() >= 4 {
                if let (Ok(x), Ok(y), Ok(z), Ok(mf)) = (
                    parts[0].parse::<f32>(),
                    parts[1].parse::<f32>(),
                    parts[2].parse::<f32>(),
                    parts[3].parse::<f32>(),
                ) {
                    nodes.push(TraceNode { x, y, z, metal_fraction: mf });
                }
            }
        }
    }

    Ok(nodes)
}

/// Run rpim_solver on an existing .pcprep file.
///
/// Returns path to solder_fatigue.json.
#[tauri::command]
async fn run_solver(
    app: AppHandle,
    pcprep_path: String,
    out_dir: String,
) -> Result<String, String> {
    std::fs::create_dir_all(&out_dir)
        .map_err(|e| format!("Cannot create out_dir: {e}"))?;

    emit(&app, "solver", "Running RPIM thermal + fatigue solver…", false, None);

    let bin = find_bin("rpim_solver")
        .ok_or_else(|| "rpim_solver binary not found".to_string())?;

    let log_path = Path::new(&out_dir).join("rpim_solver.dat");
    run_streamed(
        &app,
        "solver",
        &bin,
        &[&pcprep_path, "--output-dir", &out_dir],
        Some(&log_path),
    )?;

    Ok(format!("{out_dir}/solder_fatigue.json"))
}

// ─── app entry point ─────────────────────────────────────────────────────────

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            pick_file,
            check_model_exists,
            run_import,
            run_generate_pc,
            run_solver,
            read_json_file,
            read_trace_map,
        ])
        .run(tauri::generate_context!())
        .expect("error while running ElectroniX");
}
