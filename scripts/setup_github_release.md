# GitHub Release Setup — ElectroniX

Follow these steps **once** to wire up the automated release pipeline.

---

## 1. Create the GitHub repository

```bash
# Create repo at github.com/Solaris/ElectroniX (or your org)
gh repo create Solaris/ElectroniX --public --source=. --remote=origin
git push -u origin main
```

---

## 2. Push all submodules

The CI workflow uses `submodules: recursive`. Make sure all submodules are
committed to their respective remotes:

```bash
git submodule foreach 'git push origin HEAD:main || true'
```

---

## 3. Generate the updater signing key

```powershell
.\scripts\gen_signing_key.ps1
```

This prints a public/private key pair. Copy them to:

| Destination | Value |
|---|---|
| `tauri.conf.json` → `plugins.updater.pubkey` | The **public key** string |
| GitHub Secret `TAURI_SIGNING_PRIVATE_KEY` | The **private key** string |
| GitHub Secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | The password you chose |

---

## 4. Add GitHub Actions secrets

Go to: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | From step 3 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | From step 3 |

`GITHUB_TOKEN` is provided automatically — no action needed.

---

## 5. (Optional) Windows code-signing

Without an EV certificate, Windows SmartScreen shows "Unknown Publisher".
Users click **More info → Run anyway**. This is acceptable for beta/early access.

To remove the warning later:
1. Buy a code-signing certificate (DigiCert, Sectigo, ~$300/yr)
2. Add `WINDOWS_CERTIFICATE` and `WINDOWS_CERTIFICATE_PASSWORD` secrets
3. Update the workflow `Build Tauri app` step with the certificate env vars

---

## 6. Ship the first release

```bash
# Bump version in tauri.conf.json + package.json first, then:
git add .
git commit -m "chore: release v0.1.0"
git tag v0.1.0
git push origin main --tags
```

GitHub Actions will:
1. Build on Windows, macOS (Intel + Apple Silicon), Linux
2. Create a GitHub Release named `ElectroniX v0.1.0`
3. Attach: `.exe` installer, `.dmg` (×2), `.AppImage`, `.deb`

---

## 7. Update the `latest.json` endpoint

Tauri's updater fetches:
```
https://github.com/Solaris/ElectroniX/releases/latest/download/latest.json
```

The `tauri-action` GitHub Action generates and uploads this file automatically
as part of each release. No extra work needed.

---

## Version bump checklist

Before tagging a new release:

- [ ] Update `version` in `src-tauri/tauri.conf.json`
- [ ] Update `version` in `src-tauri/Cargo.toml`  
- [ ] Update `CHANGELOG.md` (move Unreleased → new version section)
- [ ] `git tag vX.Y.Z && git push origin main --tags`
