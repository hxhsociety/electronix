# build_stls.ps1 — Render each PBGA 256 body to its own STL.
#
# Output:
#   stl/pcb.stl, stl/pads.stl, stl/balls.stl, stl/substrate.stl,
#   stl/die_attach.stl, stl/die.stl, stl/mold.stl
#
# Prereq: OpenSCAD on PATH. Default Windows install path is added below.

$ErrorActionPreference = 'Stop'

# Locate openscad.exe
$openscad = Get-Command openscad -ErrorAction SilentlyContinue
if (-not $openscad) {
    $candidates = @(
        "C:\Program Files\OpenSCAD\openscad.exe",
        "C:\Program Files (x86)\OpenSCAD\openscad.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $openscad = $c; break } }
    if (-not $openscad) { throw "OpenSCAD not found. Install from https://openscad.org" }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scad      = Join-Path $scriptDir 'board.scad'
$out       = Join-Path $scriptDir 'stl'
New-Item -ItemType Directory -Force -Path $out | Out-Null

$bodies = @('pcb','pads','balls','substrate','die_attach','die','mold')

foreach ($b in $bodies) {
    $stl = Join-Path $out "$b.stl"
    Write-Host "Rendering $b -> $stl" -ForegroundColor Cyan
    & $openscad -o $stl -D ('RENDER="' + $b + '"') $scad
    if ($LASTEXITCODE -ne 0) { throw "OpenSCAD failed on body '$b'" }
}

Write-Host ""
Write-Host "All 7 STLs generated in $out" -ForegroundColor Green
Write-Host "Next: assemble into a STEP file with named bodies (see README.md)"
