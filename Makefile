# maude Makefile
# Provides shortcuts for common development tasks.

SHELL         := /usr/bin/env bash
VERSION       ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
PACKER_DIR    := packer
OUTPUT_DIR    := output
DOCKER_IMAGE  := ghcr.io/dirkpetersen/maude
UBUNTU_ISO_URL ?=

# ── Help ──────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@echo ""
	@echo "maude — build targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  VERSION=$(VERSION)"
	@echo ""

# ── Tests ─────────────────────────────────────────────────────────────────────
.PHONY: test test-fast lint
test: ## Run full test suite
	@bash tests/run-all-tests.sh

test-fast: ## Run tests, stop on first failure
	@bash tests/run-all-tests.sh --fail-fast

lint: ## Bash syntax check all scripts
	@bash tests/test-scripts-syntax.sh

# ── Docker ────────────────────────────────────────────────────────────────────
.PHONY: build-docker run-docker push-docker
build-docker: ## Build Docker image locally
	docker build \
		--label "org.opencontainers.image.version=$(VERSION)" \
		-t $(DOCKER_IMAGE):$(VERSION) \
		-t $(DOCKER_IMAGE):latest \
		.

run-docker: ## Run Docker container locally (web-term on :3000, SSH on :2222)
	docker run --rm -it \
		-p 3000:3000 \
		-p 2222:22 \
		-p 8080:8080 \
		--name maude \
		$(DOCKER_IMAGE):latest

push-docker: ## Push Docker image to GHCR
	docker push $(DOCKER_IMAGE):$(VERSION)
	docker push $(DOCKER_IMAGE):latest

# ── WSL ───────────────────────────────────────────────────────────────────────
.PHONY: build-wsl
build-wsl: ## Build WSL rootfs tarball (requires debootstrap, Linux only)
	@command -v debootstrap >/dev/null || { echo "Install debootstrap first"; exit 1; }
	@bash -c ' \
		set -euo pipefail; \
		ARTIFACT="maude-wsl-ubuntu2604-$(VERSION).tar.gz"; \
		sudo mkdir -p /tmp/maude-rootfs; \
		sudo debootstrap \
			--arch=amd64 --variant=minbase \
			--include=ca-certificates,curl,wget,git,python3,python3-pip,python3-venv,\
nodejs,npm,golang-go,tmux,openssh-server,sudo,bash-completion,vim,jq,build-essential \
			plucky /tmp/maude-rootfs http://archive.ubuntu.com/ubuntu/; \
		sudo mkdir -p /tmp/maude-rootfs/etc/maude; \
		sudo cp scripts/profile.d/maude-path.sh /tmp/maude-rootfs/etc/profile.d/; \
		sudo cp scripts/profile.d/maude-firstlogin.sh /tmp/maude-rootfs/etc/profile.d/; \
		sudo cp scripts/new-user-login.sh /tmp/maude-rootfs/etc/maude/; \
		sudo cp scripts/first-boot.sh /tmp/maude-rootfs/etc/maude/; \
		sudo cp scripts/maude-setup /tmp/maude-rootfs/usr/local/bin/; \
		sudo cp scripts/maude-adduser /tmp/maude-rootfs/usr/local/bin/; \
		sudo chmod 755 /tmp/maude-rootfs/etc/maude/*.sh; \
		sudo chmod 755 /tmp/maude-rootfs/usr/local/bin/maude-*; \
		printf "MAUDE_VERSION=$(VERSION)\nMAUDE_DEPLOY_TARGET=wsl\n" \
			| sudo tee /tmp/maude-rootfs/etc/maude/maude.conf; \
		sudo tar -czf "$(OUTPUT_DIR)/$$ARTIFACT" \
			--numeric-owner -C /tmp/maude-rootfs .; \
		sha256sum "$(OUTPUT_DIR)/$$ARTIFACT" > "$(OUTPUT_DIR)/$$ARTIFACT.sha256"; \
		sudo rm -rf /tmp/maude-rootfs; \
		echo "Built: $(OUTPUT_DIR)/$$ARTIFACT"; \
	'

# ── VM (Packer) ───────────────────────────────────────────────────────────────
.PHONY: build-vm build-vm-kvm build-vm-vmware packer-init
packer-init: ## Initialize Packer plugins
	cd $(PACKER_DIR) && packer init .

build-vm: packer-init ## Build both KVM and VMware VM images
	@[[ -n "$(UBUNTU_ISO_URL)" ]] || { echo "Set UBUNTU_ISO_URL=<url>"; exit 1; }
	cd $(PACKER_DIR) && packer build \
		-var "iso_url=$(UBUNTU_ISO_URL)" \
		-var "iso_checksum=none" \
		-var "maude_version=$(VERSION)" \
		-var "output_dir=../$(OUTPUT_DIR)" \
		.

build-vm-kvm: packer-init ## Build KVM/QEMU image only (.qcow2)
	@[[ -n "$(UBUNTU_ISO_URL)" ]] || { echo "Set UBUNTU_ISO_URL=<url>"; exit 1; }
	cd $(PACKER_DIR) && packer build \
		-only="qemu.ubuntu_2604" \
		-var "iso_url=$(UBUNTU_ISO_URL)" \
		-var "iso_checksum=none" \
		-var "maude_version=$(VERSION)" \
		-var "output_dir=../$(OUTPUT_DIR)" \
		.

build-vm-vmware: packer-init ## Build VMware image only (.ova)
	@[[ -n "$(UBUNTU_ISO_URL)" ]] || { echo "Set UBUNTU_ISO_URL=<url>"; exit 1; }
	cd $(PACKER_DIR) && packer build \
		-only="vmware-iso.ubuntu_2604" \
		-var "iso_url=$(UBUNTU_ISO_URL)" \
		-var "iso_checksum=none" \
		-var "maude_version=$(VERSION)" \
		-var "output_dir=../$(OUTPUT_DIR)" \
		.

# ── Release ───────────────────────────────────────────────────────────────────
.PHONY: release tag
tag: ## Create and push a version tag (e.g. make tag VERSION=v0.1.0)
	@[[ "$(VERSION)" == v* ]] || { echo "VERSION must start with 'v' (e.g. v0.1.0)"; exit 1; }
	git tag -a "$(VERSION)" -m "Release $(VERSION)"
	git push origin "$(VERSION)"

# ── Housekeeping ──────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove build outputs
	rm -rf $(OUTPUT_DIR)
	docker rmi $(DOCKER_IMAGE):$(VERSION) $(DOCKER_IMAGE):latest 2>/dev/null || true

.DEFAULT_GOAL := help
