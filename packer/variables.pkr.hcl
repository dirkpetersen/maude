# Packer variables for maude Ubuntu 26.04 image builds

variable "ubuntu_version" {
  type    = string
  default = "26.04"
}

variable "ubuntu_codename" {
  type    = string
  default = "plucky"
}

variable "iso_url" {
  type        = string
  description = "URL to Ubuntu 26.04 server ISO. Override to point at a local mirror or beta ISO."
  # Ubuntu 26.04 (Plucky Puffin) — update checksum after GA release
  default = "https://releases.ubuntu.com/plucky/ubuntu-26.04-live-server-amd64.iso"
}

variable "iso_checksum" {
  type        = string
  description = "SHA256 checksum of the ISO. Get from https://releases.ubuntu.com/plucky/SHA256SUMS"
  default     = "none"  # Set to actual checksum or override via env: PKR_VAR_iso_checksum
}

variable "vm_name" {
  type    = string
  default = "maude-ubuntu-2604"
}

variable "disk_size" {
  type        = number
  description = "Disk size in MiB"
  default     = 20480  # 20 GiB
}

variable "memory" {
  type        = number
  description = "RAM in MiB for build VM"
  default     = 4096
}

variable "cpus" {
  type    = number
  default = 2
}

variable "ssh_username" {
  type    = string
  default = "maude"
}

variable "ssh_password" {
  type      = string
  default   = "maude"
  sensitive = true
}

variable "output_dir" {
  type    = string
  default = "output"
}

variable "headless" {
  type    = bool
  default = true
}

variable "maude_version" {
  type        = string
  description = "maude release version tag (e.g. v0.1.0)"
  default     = "dev"
}
