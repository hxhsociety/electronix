# ============================================================
#  ElectroniX — Build pipeline (Linux / macOS / CI)
#
#  Targets:
#    make          → debug build + package
#    make release  → optimised build + package
#    make rust     → Rust workspace only (debug)
#    make frontend → frontend only
#    make clean    → remove all build artefacts
#    make check    → cargo check + tsc --noEmit (fast lint)
#    make fmt      → cargo fmt + (frontend) prettier
# ============================================================

SHELL   := /bin/bash
ROOT    := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
DIST    := $(ROOT)_dist
FRONT   := $(ROOT)frontend

# Detect release vs debug
ifdef RELEASE
  CARGO_FLAGS := --release
  BIN_DIR     := $(ROOT)target/release
else
  CARGO_FLAGS :=
  BIN_DIR     := $(ROOT)target/debug
endif

BINARIES := gltf_convertor rpim_pc rpim_solver

# ── Default: full build ──────────────────────────────────────
.PHONY: all
all: rust frontend package

# ── Release shortcut ─────────────────────────────────────────
.PHONY: release
release:
	$(MAKE) RELEASE=1 all

# ── 1. Rust workspace ────────────────────────────────────────
.PHONY: rust
rust:
	@echo ""
	@echo "══ Building Rust workspace ($(if $(RELEASE),release,debug)) ══"
	cargo build --workspace $(CARGO_FLAGS)
	@echo ""
	@for b in $(BINARIES); do \
	    exe="$(BIN_DIR)/$$b"; \
	    if [ -f "$$exe" ]; then \
	        sz=$$(du -sh "$$exe" | cut -f1); \
	        echo "  ✔  $$b  ($$sz)"; \
	    else \
	        echo "  ✖  $$b  NOT FOUND"; exit 1; \
	    fi; \
	done

# ── 2. Frontend ──────────────────────────────────────────────
.PHONY: frontend
frontend:
	@echo ""
	@echo "══ Building frontend (Vite) ══"
	cd $(FRONT) && npm install && npm run build
	@echo "  ✔  dist/ built"

# ── 3. Package ───────────────────────────────────────────────
.PHONY: package
package:
	@echo ""
	@echo "══ Packaging → _dist/ ══"
	rm -rf $(DIST)
	mkdir -p $(DIST)/public $(DIST)/workspace
	@for b in $(BINARIES); do \
	    cp "$(BIN_DIR)/$$b" $(DIST)/; \
	    echo "  copied $$b"; \
	done
	cp -r $(FRONT)/dist/. $(DIST)/public/
	@echo "  ✔  Package complete → $(DIST)"

# ── check (no build artefacts) ───────────────────────────────
.PHONY: check
check:
	cargo check --workspace
	cd $(FRONT) && node node_modules/typescript/bin/tsc --noEmit
	@echo "  ✔  All checks passed"

# ── fmt ──────────────────────────────────────────────────────
.PHONY: fmt
fmt:
	cargo fmt --all
	@echo "  ✔  Rust formatted"

# ── clean ────────────────────────────────────────────────────
.PHONY: clean
clean:
	cargo clean
	rm -rf $(FRONT)/dist _dist
	@echo "  ✔  Cleaned"
