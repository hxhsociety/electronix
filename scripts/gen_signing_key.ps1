# gen_signing_key.ps1 - Generate a Tauri updater signing key pair.
#
# Run ONCE before your first release:
#   .\scripts\gen_signing_key.ps1
#
# It will print the public key (goes in tauri.conf.json -> plugins.updater.pubkey)
# and the private key + password (go into GitHub Actions secrets).
#
# Prerequisites: `cargo tauri` CLI (npm install -g @tauri-apps/cli)

$ErrorActionPreference = 'Stop'

Write-Host ""
Write-Host "ElectroniX - Tauri updater key generation" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

# Check tauri CLI is available
if (-not (Get-Command "tauri" -ErrorAction SilentlyContinue)) {
    # Try via npx
    $tauriCmd = "npx @tauri-apps/cli"
} else {
    $tauriCmd = "tauri"
}

Write-Host "Generating key pair via: $tauriCmd signer generate" -ForegroundColor Yellow
Write-Host "(You will be prompted for a password - save it!)"
Write-Host ""

Invoke-Expression "$tauriCmd signer generate"

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Green
Write-Host "1. Copy the PUBLIC KEY above into tauri.conf.json:"
Write-Host '     "plugins": { "updater": { "pubkey": "<PASTE PUBLIC KEY HERE>" } }'
Write-Host ""
Write-Host "2. Add these to GitHub -> Settings -> Secrets -> Actions:"
Write-Host "     TAURI_SIGNING_PRIVATE_KEY        (the private key string)"
Write-Host "     TAURI_SIGNING_PRIVATE_KEY_PASSWORD (the password you chose)"
Write-Host ""
Write-Host "3. Push a version tag to trigger the release workflow:"
Write-Host "     git tag v0.1.0 && git push origin v0.1.0"
Write-Host ""
