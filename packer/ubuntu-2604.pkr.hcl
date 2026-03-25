# maude — Packer template for Ubuntu 26.04 VM images
# Outputs:
#   output/qemu/   → .qcow2 for KVM/Proxmox
#   output/vmware/ → .ova  for VMware (requires vmware-iso plugin + VMware Workstation/Fusion)
#
# Usage:
#   packer init .
#   packer build -var "iso_checksum=sha256:<hash>" .
#
# For beta/daily ISO, override iso_url:
#   packer build -var "iso_url=https://cdimage.ubuntu.com/ubuntu-server/daily-live/current/plucky-live-server-amd64.iso" \
#                -var "iso_checksum=none" .

packer {
  required_version = ">= 1.10.0"
  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = ">= 1.0.0"
    }
    vmware = {
      source  = "github.com/hashicorp/vmware"
      version = ">= 1.0.0"
    }
  }
}

# ── Locals ────────────────────────────────────────────────────────────────────

locals {
  build_timestamp = formatdate("YYYYMMDD-hhmm", timestamp())
  image_name      = "${var.vm_name}-${var.maude_version}"
}

# ── QEMU / KVM source ─────────────────────────────────────────────────────────

source "qemu" "ubuntu_2604" {
  vm_name          = "${local.image_name}"
  iso_url          = var.iso_url
  iso_checksum     = var.iso_checksum
  output_directory = "${var.output_dir}/qemu"

  disk_size        = var.disk_size
  memory           = var.memory
  cpus             = var.cpus
  headless         = var.headless
  accelerator      = "kvm"   # use "none" if KVM unavailable on build host
  disk_interface   = "virtio"
  net_device       = "virtio-net"
  format           = "qcow2"

  # Autoinstall via HTTP server
  http_directory   = "${path.root}/http"
  boot_wait        = "5s"
  boot_command = [
    "e<wait>",
    "<down><down><down>",
    "<end>",
    " autoinstall ds=nocloud-net;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<F10>"
  ]

  ssh_username         = var.ssh_username
  ssh_password         = var.ssh_password
  ssh_timeout          = "30m"
  ssh_handshake_attempts = 50
  shutdown_command     = "echo '${var.ssh_password}' | sudo -S shutdown -P now"
}

# ── VMware source ─────────────────────────────────────────────────────────────

source "vmware-iso" "ubuntu_2604" {
  vm_name          = "${local.image_name}"
  iso_url          = var.iso_url
  iso_checksum     = var.iso_checksum
  output_directory = "${var.output_dir}/vmware"

  disk_size        = var.disk_size
  memory           = var.memory
  cpus             = var.cpus
  headless         = var.headless
  disk_type_id     = 0   # growable virtual disk

  http_directory   = "${path.root}/http"
  boot_wait        = "5s"
  boot_command = [
    "e<wait>",
    "<down><down><down>",
    "<end>",
    " autoinstall ds=nocloud-net;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<F10>"
  ]

  ssh_username         = var.ssh_username
  ssh_password         = var.ssh_password
  ssh_timeout          = "30m"
  shutdown_command     = "echo '${var.ssh_password}' | sudo -S shutdown -P now"

  # Export as OVA after build
  skip_export      = false
  export_config    = {
    format          = "ova"
    output_dir      = "${var.output_dir}/vmware"
    ovftool_options = ["--compress=9"]
  }
}

# ── Build ─────────────────────────────────────────────────────────────────────

build {
  name    = "maude"
  sources = [
    "source.qemu.ubuntu_2604",
    "source.vmware-iso.ubuntu_2604",
  ]

  # ── Provisioner 1: Wait for cloud-init to finish ──
  provisioner "shell" {
    inline = [
      "cloud-init status --wait || true",
      "sudo apt-get update -qq",
    ]
  }

  # ── Provisioner 2: Install packages from maude package list ──
  provisioner "file" {
    source      = "${path.root}/../packages/ubuntu-packages.yaml"
    destination = "/tmp/ubuntu-packages.yaml"
  }

  provisioner "shell" {
    inline = [
      "sudo apt-get install -y python3-yaml 2>/dev/null || true",
      # Parse YAML package list and install with apt
      "python3 -c \"import yaml,subprocess,sys; pkgs=yaml.safe_load(open('/tmp/ubuntu-packages.yaml'))['packages']; subprocess.run(['sudo','apt-get','install','-y','--no-install-recommends']+pkgs, check=True)\" || true",
      "sudo apt-get autoremove -y",
      "sudo apt-get clean",
    ]
  }

  # ── Provisioner 3: Upload maude scripts ──
  provisioner "file" {
    source      = "${path.root}/../scripts/"
    destination = "/tmp/maude-scripts"
  }

  # ── Provisioner 4: Install maude scripts and config ──
  provisioner "shell" {
    inline = [
      # Install maude scripts
      "sudo mkdir -p /etc/maude/profile.d",
      "sudo cp /tmp/maude-scripts/first-boot.sh /etc/maude/first-boot.sh",
      "sudo cp /tmp/maude-scripts/new-user-login.sh /etc/maude/new-user-login.sh",
      "sudo cp /tmp/maude-scripts/maude-setup /usr/local/bin/maude-setup",
      "sudo cp /tmp/maude-scripts/maude-adduser /usr/local/bin/maude-adduser",
      "sudo cp /tmp/maude-scripts/profile.d/*.sh /etc/profile.d/",
      "sudo chmod 755 /etc/maude/first-boot.sh /etc/maude/new-user-login.sh",
      "sudo chmod 755 /usr/local/bin/maude-setup /usr/local/bin/maude-adduser",
      "sudo chmod 644 /etc/profile.d/maude-*.sh",
      # Create base config
      "sudo mkdir -p /etc/maude",
      "echo 'MAUDE_VERSION=${var.maude_version}' | sudo tee /etc/maude/maude.conf",
      "echo 'MAUDE_BUILD_DATE=${local.build_timestamp}' | sudo tee -a /etc/maude/maude.conf",
    ]
  }

  # ── Provisioner 5: Install systemd first-boot service ──
  provisioner "shell" {
    inline = [
      "sudo tee /etc/systemd/system/maude-first-boot.service <<'EOF'",
      "[Unit]",
      "Description=maude first-boot setup",
      "After=network-online.target",
      "Wants=network-online.target",
      "ConditionPathExists=!/etc/maude/.first-boot-done",
      "",
      "[Service]",
      "Type=oneshot",
      "ExecStart=/etc/maude/first-boot.sh",
      "RemainAfterExit=yes",
      "StandardOutput=journal+console",
      "StandardError=journal+console",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "EOF",
      "sudo systemctl enable maude-first-boot.service",
    ]
  }

  # ── Provisioner 6: Harden SSH ──
  provisioner "shell" {
    inline = [
      "sudo sed -i 's/^#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config",
      "sudo sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config",
      "sudo sed -i 's/^#MaxAuthTries.*/MaxAuthTries 5/' /etc/ssh/sshd_config",
      "sudo sed -i 's/^#LoginGraceTime.*/LoginGraceTime 30/' /etc/ssh/sshd_config",
      "echo 'AllowAgentForwarding yes' | sudo tee -a /etc/ssh/sshd_config",
      "echo 'PrintLastLog yes' | sudo tee -a /etc/ssh/sshd_config",
    ]
  }

  # ── Provisioner 7: Remove build-time sudoers, clean up ──
  provisioner "shell" {
    inline = [
      "sudo rm -f /etc/sudoers.d/maude-build",
      "sudo rm -rf /tmp/maude-scripts /tmp/ubuntu-packages.yaml",
      # Zero free space for smaller image (optional but reduces artifact size)
      "sudo dd if=/dev/zero of=/zero bs=1M 2>/dev/null || true",
      "sudo sync",
      "sudo rm -f /zero",
      # Remove SSH host keys (regenerated on first boot)
      "sudo rm -f /etc/ssh/ssh_host_*",
      "echo 'dpkg-reconfigure openssh-server' | sudo tee /etc/rc.local",
      "sudo chmod +x /etc/rc.local",
    ]
  }

  # ── Post-processors ───────────────────────────────────────────────────────
  post-processor "checksum" {
    checksum_types      = ["sha256"]
    output              = "${var.output_dir}/{{.BuildName}}/{{.BuildName}}.sha256"
    keep_input_artifact = true
  }
}
