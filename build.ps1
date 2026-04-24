# ============================================================
#  ElectroniX — Full build pipeline (Windows PowerShell)
#
#  Usage:
#    .\build.ps1              # debug build
#    .\build.ps1 -Release     # optimised release build
#    .\build.ps1 -Release -SkipFrontend  # Rust only
#    .\build.ps1 -FrontendOnly           # frontend only
#
# ============================================================
param(
    [switch]$Release,
    [switch]$SkipFrontend,
    [switch]$FrontendOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root     = $PSScriptRoot
$BinSuffix = if ($Release) { 'release' } else { 'debug' }
$BinDir   = Join-Path $Root "target\$BinSuffix"
$DistDir  = Join-Path $Root "_dist"
$FrontDir = Join-Path $Root "frontend"

function Write-Step([string]$msg) {
    Write-Host "`n══ $msg " -ForegroundColor Cyan -NoNewline
    Write-Host ('═' * [Math]::Max(2, 55 - $msg.Length)) -ForegroundColor Cyan
}

function Assert-Ok([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n✖  $step failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ── 0. Banner ────────────────────────────────────────────────
Write-Host ""
Write-Host "  ElectroniX Build Pipeline" -ForegroundColor White
Write-Host "  Mode   : $(if ($Release) { 'RELEASE' } else { 'DEBUG' })" -ForegroundColor $(if ($Release) { 'Green' } else { 'Yellow' })
Write-Host "  Root   : $Root"
Write-Host "  Output : $DistDir"
Write-Host ""

Set-Location $Root

# ── 1. Rust workspace ────────────────────────────────────────
if (-not $FrontendOnly) {
    Write-Step "1/3  Building Rust workspace"

    $cargoArgs = @('build', '--workspace')
    if ($Release) { $cargoArgs += '--release' }

    & cargo @cargoArgs
    Assert-Ok "cargo build"

    Write-Host ""
    $binaries = @('gltf_convertor', 'rpim_pc', 'rpim_solver')
    foreach ($b in $binaries) {
        $exe = Join-Path $BinDir "$b.exe"
        if (Test-Path $exe) {
            $sz = [Math]::Round((Get-Item $exe).Length / 1MB, 1)
            Write-Host "  ✔  $b.exe  ($sz MB)" -ForegroundColor Green
        } else {
            Write-Host "  ✖  $b.exe  NOT FOUND" -ForegroundColor Red
            exit 1
        }
    }
}

# ── 2. Frontend ──────────────────────────────────────────────
if (-not $SkipFrontend) {
    Write-Step "2/3  Building frontend (Vite)"

    Set-Location $FrontDir

    # Install deps if node_modules is missing or package-lock changed
    if (-not (Test-Path 'node_modules') -or
        (Get-Item 'package-lock.json').LastWriteTime -gt (Get-Item 'node_modules\.package-lock.json' -ErrorAction SilentlyContinue)?.LastWriteTime) {
        Write-Host "  npm install..." -ForegroundColor DarkGray
        & npm install --silent
        Assert-Ok "npm install"
    }

    & npm run build
    Assert-Ok "npm run build"

    $distJs = Get-ChildItem (Join-Path $FrontDir 'dist\assets') -Filter '*.js' -ErrorAction SilentlyContinue
    if ($distJs) {
        $sz = [Math]::Round(($distJs | Measure-Object Length -Sum).Sum / 1KB, 0)
        Write-Host "  ✔  dist/ built  ($sz KB JS)" -ForegroundColor Green
    }

    Set-Location $Root
}

# ── 3. Package ───────────────────────────────────────────────
if (-not $FrontendOnly -and -not $SkipFrontend) {
    Write-Step "3/3  Packaging → _dist\"

    # Clean and recreate
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
    New-Item -ItemType Directory $DistDir             | Out-Null
    New-Item -ItemType Directory "$DistDir\public"    | Out-Null
    New-Item -ItemType Directory "$DistDir\workspace" | Out-Null

    # Binaries
    foreach ($b in @('gltf_convertor', 'rpim_pc', 'rpim_solver')) {
        Copy-Item (Join-Path $BinDir "$b.exe") $DistDir
    }

    # Frontend static assets (index.html + assets/)
    Copy-Item -Recurse (Join-Path $FrontDir 'dist\*') "$DistDir\public"

    # Launcher script (opens browser + serves frontend via PowerShell)
    $launcher = @'
# ElectroniX Launcher — starts a local file server and opens the app
$port = 5173
$pub  = Join-Path $PSScriptRoot 'public'
Start-Process "http://localhost:$port"
Write-Host "ElectroniX running at http://localhost:$port"
Write-Host "Press Ctrl-C to quit."
# Simple static server via .NET HttpListener
$http = [System.Net.HttpListener]::new()
$http.Prefixes.Add("http://localhost:${port}/")
$http.Start()
while ($http.IsListening) {
    $ctx  = $http.GetContext()
    $req  = $ctx.Request
    $resp = $ctx.Response
    $path = $req.Url.LocalPath.TrimStart('/')
    if ($path -eq '') { $path = 'index.html' }
    $file = Join-Path $pub $path
    if (Test-Path $file -PathType Leaf) {
        $bytes = [System.IO.File]::ReadAllBytes($file)
        $ext   = [System.IO.Path]::GetExtension($file).ToLower()
        $mime  = switch ($ext) {
            '.html' { 'text/html' }; '.js' { 'application/javascript' }
            '.css'  { 'text/css' };  '.wasm'{ 'application/wasm' }
            '.glb'  { 'model/gltf-binary' }; '.json' { 'application/json' }
            '.csv'  { 'text/csv' }; '.svg' { 'image/svg+xml' }
            default { 'application/octet-stream' }
        }
        $resp.ContentType   = $mime
        $resp.ContentLength64 = $bytes.Length
        $resp.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $resp.StatusCode = 404
    }
    $resp.Close()
}
'@
    $launcher | Out-File -Encoding utf8 "$DistDir\run.ps1"

    # Summary
    Write-Host ""
    Write-Host "  _dist\" -ForegroundColor White
    Get-ChildItem $DistDir -Recurse | ForEach-Object {
        $rel = $_.FullName.Replace($DistDir, '').TrimStart('\')
        $sz  = if ($_.PSIsContainer) { '' } else { "  $([Math]::Round($_.Length/1KB,0)) KB" }
        Write-Host "    $rel$sz" -ForegroundColor $(if ($_.PSIsContainer) { 'DarkGray' } else { 'Gray' })
    }

    Write-Host ""
    Write-Host "  ✔  Build complete" -ForegroundColor Green
    Write-Host "  ➜  Run: .\_dist\run.ps1" -ForegroundColor Cyan
}
