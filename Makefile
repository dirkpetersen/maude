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
WSL_DISTRO  ?= maude-dev
WSL_DIR     ?= C:\maude-dev

.PHONY: build-wsl build-wsl-local wsl-import wsl-test wsl-update-scripts

build-wsl: ## Build WSL tarball locally via Docker (same method as CI, works on Linux/WSL/Mac)
	@command -v docker >/dev/null || { echo "Docker required"; exit 1; }
	@mkdir -p $(OUTPUT_DIR)
	$(eval ARTIFACT := maude-wsl-ubuntu2604-$(VERSION).tar.gz)
	@echo "Building WSL rootfs via ubuntu:plucky container..."
	@docker rm -f maude-wsl-build 2>/dev/null || true
	docker run --name maude-wsl-build \
		-v "$(CURDIR):/maude-src:ro" \
		-e MAUDE_VERSION="$(VERSION)" \
		-e MAUDE_BUILD_DATE="$$(date -Iseconds)" \
		ubuntu:plucky bash -c '\
			set -e; \
			export DEBIAN_FRONTEND=noninteractive TZ=UTC; \
			apt-get update -qq; \
			apt-get install -y --no-install-recommends \
				ca-certificates curl wget sudo locales tzdata git \
				python3 python3-pip python3-venv nodejs npm golang-go \
				tmux openssh-server openssh-client bash-completion \
				vim jq build-essential pkg-config libssl-dev libffi-dev \
				net-tools bind9-dnsutils iputils-ping iproute2 \
				unzip zip ripgrep ufw fail2ban; \
			apt-get clean; rm -rf /var/lib/apt/lists/*; \
			locale-gen en_US.UTF-8; echo LANG=en_US.UTF-8 > /etc/default/locale; \
			echo maude > /etc/hostname; \
			groupadd --gid 100 users 2>/dev/null || true; \
			useradd --create-home --shell /bin/bash --gid users maude; \
			echo "maude:maude" | chpasswd; \
			echo "maude ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/maude; \
			chmod 440 /etc/sudoers.d/maude; \
			_arch=$$(dpkg --print-architecture); \
			curl -fsSL "https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-$${_arch}" \
				-o /usr/local/bin/mom && chmod 4755 /usr/local/bin/mom; \
			mkdir -p /etc/mom /etc/maude; \
			printf "group = \"users\"\ndeny_list = \"/etc/mom/deny.list\"\nlog_file = \"/var/log/mom.log\"\n" \
				> /etc/mom/mom.conf; \
			printf "nmap\ntcpdump\nwireshark*\n" > /etc/mom/deny.list; \
			cp /maude-src/scripts/profile.d/maude-path.sh       /etc/profile.d/; \
			cp /maude-src/scripts/profile.d/maude-firstlogin.sh  /etc/profile.d/; \
			cp /maude-src/scripts/new-user-login.sh              /etc/maude/; \
			cp /maude-src/scripts/first-boot.sh                  /etc/maude/; \
			cp /maude-src/scripts/maude-setup                    /usr/local/bin/; \
			cp /maude-src/scripts/maude-adduser                  /usr/local/bin/; \
			chmod 644 /etc/profile.d/maude-*.sh; \
			chmod 755 /etc/maude/new-user-login.sh /etc/maude/first-boot.sh; \
			chmod 755 /usr/local/bin/maude-setup /usr/local/bin/maude-adduser; \
			printf "\n# maude: enforce correct PATH order (runs last)\nif [ -f /etc/profile.d/maude-path.sh ]; then . /etc/profile.d/maude-path.sh; fi\n" \
				>> /home/maude/.bashrc; \
			printf "\n# maude: enforce correct PATH order (runs last)\nif [ -f /etc/profile.d/maude-path.sh ]; then . /etc/profile.d/maude-path.sh; fi\n" \
				>> /etc/skel/.bashrc; \
			printf "MAUDE_VERSION=$${MAUDE_VERSION}\nMAUDE_BUILD_DATE=$${MAUDE_BUILD_DATE}\nMAUDE_DEPLOY_TARGET=wsl\n" \
				> /etc/maude/maude.conf; \
			printf "[boot]\nsystemd=true\ncommand=/etc/maude/first-boot.sh\n\n[network]\nhostname=maude\n\n[user]\ndefault=maude\n\n[interop]\nenabled=true\nappendWindowsPath=false\n" \
				> /etc/wsl.conf; \
			echo "WSL rootfs build complete."; \
		'
	docker export maude-wsl-build | gzip > $(OUTPUT_DIR)/$(ARTIFACT)
	docker rm maude-wsl-build
	sha256sum $(OUTPUT_DIR)/$(ARTIFACT) > $(OUTPUT_DIR)/$(ARTIFACT).sha256
	@echo ""
	@echo "Built: $(OUTPUT_DIR)/$(ARTIFACT)"
	@echo "Size:  $$(du -sh $(OUTPUT_DIR)/$(ARTIFACT) | cut -f1)"
	@echo ""
	@echo "To import and test:"
	@echo "  make wsl-import   (Linux/WSL: uses wsl.exe)"
	@echo "  wsl --import $(WSL_DISTRO) $(WSL_DIR) $(OUTPUT_DIR)/$(ARTIFACT) --version 2"

wsl-import: ## Import locally-built WSL image as '$(WSL_DISTRO)' (runs wsl.exe from WSL)
	$(eval ARTIFACT := maude-wsl-ubuntu2604-$(VERSION).tar.gz)
	@[[ -f "$(OUTPUT_DIR)/$(ARTIFACT)" ]] || { echo "Run 'make build-wsl' first"; exit 1; }
	@echo "Terminating existing $(WSL_DISTRO) if running..."
	wsl.exe --terminate $(WSL_DISTRO) 2>/dev/null || true
	wsl.exe --unregister $(WSL_DISTRO) 2>/dev/null || true
	wsl.exe --import $(WSL_DISTRO) $(WSL_DIR) "$$(wslpath -w $(OUTPUT_DIR)/$(ARTIFACT))" --version 2
	@echo ""
	@echo "Imported. Start with:  wsl.exe -d $(WSL_DISTRO)"

wsl-test: ## Run basic smoke tests inside the WSL dev distro
	@echo "=== Smoke tests for $(WSL_DISTRO) ==="
	wsl.exe -d $(WSL_DISTRO) -- bash -lc 'echo "PATH: $$PATH"'
	@echo ""
	wsl.exe -d $(WSL_DISTRO) -- bash -lc 'p=$$PATH; first=$${p%%:*}; [[ "$$first" == *"/bin" ]] && echo "PASS: ~/bin is first ($$first)" || echo "FAIL: ~/bin not first, got: $$first"'
	wsl.exe -d $(WSL_DISTRO) -- bash -lc 'count=$$(echo $$PATH | tr : "\n" | grep -c "\.local/bin"); [[ $$count -eq 1 ]] && echo "PASS: .local/bin appears once" || echo "FAIL: .local/bin appears $$count times"'
	wsl.exe -d $(WSL_DISTRO) -- bash -lc 'hostname | grep -q maude && echo "PASS: hostname=maude" || echo "FAIL: hostname=$$(hostname)"'
	wsl.exe -d $(WSL_DISTRO) -- bash -lc '[[ -x /usr/local/bin/mom ]] && echo "PASS: mom installed" || echo "FAIL: mom missing"'
	wsl.exe -d $(WSL_DISTRO) -- bash -lc 'id | grep -q "users" && echo "PASS: maude user in users group" || echo "FAIL: users group missing"'

wsl-update-scripts: ## Sync changed scripts into running WSL dev distro (no rebuild needed)
	@echo "Syncing scripts into $(WSL_DISTRO)..."
	@for f in scripts/profile.d/maude-path.sh scripts/profile.d/maude-firstlogin.sh; do \
		wsl.exe -d $(WSL_DISTRO) -- sudo cp "$$(wslpath "$(CURDIR)/$$f")" /etc/profile.d/; \
		echo "  updated /etc/profile.d/$$(basename $$f)"; \
	done
	@for f in scripts/new-user-login.sh scripts/first-boot.sh; do \
		wsl.exe -d $(WSL_DISTRO) -- sudo cp "$$(wslpath "$(CURDIR)/$$f")" /etc/maude/; \
		wsl.exe -d $(WSL_DISTRO) -- sudo chmod 755 /etc/maude/$$(basename $$f); \
		echo "  updated /etc/maude/$$(basename $$f)"; \
	done
	@for f in scripts/maude-setup scripts/maude-adduser; do \
		wsl.exe -d $(WSL_DISTRO) -- sudo cp "$$(wslpath "$(CURDIR)/$$f")" /usr/local/bin/; \
		wsl.exe -d $(WSL_DISTRO) -- sudo chmod 755 /usr/local/bin/$$(basename $$f); \
		echo "  updated /usr/local/bin/$$(basename $$f)"; \
	done
	@echo "Done. Open a new shell in $(WSL_DISTRO) to test."

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
