# maude — Docker image
# Ubuntu 26.04 sandbox appliance for agentic coding.
#
# appmotel and web-term are installed at first container start (not build time)
# because appmotel's installer requires systemd to register services.
# The entrypoint handles first-run setup via /etc/maude/first-boot.sh.
#
# Build:  docker build -t ghcr.io/dirkpetersen/maude:latest .
# Run:    docker run -d -p 3000:3000 -p 8080:8080 --name maude \
#           -e MAUDE_BASE_DOMAIN=localhost ghcr.io/dirkpetersen/maude:latest

FROM ubuntu:plucky AS base

LABEL org.opencontainers.image.title="maude"
LABEL org.opencontainers.image.description="Ready-to-run sandbox appliance for agentic coding"
LABEL org.opencontainers.image.source="https://github.com/dirkpetersen/maude"
LABEL org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC
ENV MAUDE_DEPLOY_TARGET=docker

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg lsb-release \
    git git-lfs \
    build-essential pkg-config \
    python3 python3-pip python3-venv python3-dev \
    nodejs npm \
    golang-go \
    tmux \
    openssh-server openssh-client \
    sudo \
    jq tree file less \
    bash-completion \
    vim nano \
    net-tools bind9-dnsutils iputils-ping iproute2 \
    ripgrep \
    unzip zip xz-utils \
    libssl-dev libffi-dev \
    procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── SSH setup ─────────────────────────────────────────────────────────────────
RUN mkdir -p /run/sshd \
    && ssh-keygen -A \
    && sed -i \
        -e 's/^#PermitRootLogin.*/PermitRootLogin no/' \
        -e 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' \
        -e 's/^#MaxAuthTries.*/MaxAuthTries 5/' \
        -e 's/^#LoginGraceTime.*/LoginGraceTime 30/' \
        /etc/ssh/sshd_config

# ── maude scripts ─────────────────────────────────────────────────────────────
RUN mkdir -p /etc/maude
COPY scripts/profile.d/maude-path.sh        /etc/profile.d/maude-path.sh
COPY scripts/profile.d/maude-firstlogin.sh  /etc/profile.d/maude-firstlogin.sh
COPY scripts/new-user-login.sh              /etc/maude/new-user-login.sh
COPY scripts/first-boot.sh                  /etc/maude/first-boot.sh
COPY scripts/maude-setup                    /usr/local/bin/maude-setup
COPY scripts/maude-adduser                  /usr/local/bin/maude-adduser

RUN chmod 644 /etc/profile.d/maude-*.sh \
    && chmod 755 /etc/maude/new-user-login.sh \
                 /etc/maude/first-boot.sh \
                 /usr/local/bin/maude-setup \
                 /usr/local/bin/maude-adduser

# ── Default maude user ────────────────────────────────────────────────────────
RUN useradd --create-home --shell /bin/bash --comment "maude default user" maude \
    && echo 'maude:maude' | chpasswd \
    && echo 'maude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/maude \
    && chmod 440 /etc/sudoers.d/maude

# ── mom: group=users (GID 100), binary pre-installed ─────────────────────────
RUN groupadd --gid 100 users 2>/dev/null || true \
    && usermod -aG users maude \
    && mkdir -p /etc/mom \
    && printf 'group = "users"\ndeny_list = "/etc/mom/deny.list"\nlog_file = "/var/log/mom.log"\n' \
       > /etc/mom/mom.conf \
    && printf 'nmap\ntcpdump\nwireshark*\naircrack*\nmetasploit*\n' > /etc/mom/deny.list \
    && { curl -fsSL --max-time 30 \
       "https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-amd64" \
       -o /usr/local/bin/mom \
    && chmod 4755 /usr/local/bin/mom \
    || echo "WARNING: mom release not yet published — docker-entrypoint.sh will install it at runtime."; }

# ── Write maude.conf ──────────────────────────────────────────────────────────
RUN printf 'MAUDE_DEPLOY_TARGET=docker\nMAUDE_BASE_DOMAIN=localhost\n' \
    > /etc/maude/maude.conf

# ── Hook maude-path.sh at END of .bashrc (wins over Ubuntu defaults + tool prepends) ──
RUN printf '\n# maude: enforce correct PATH order (runs last, overrides any earlier prepends)\n[ -f /etc/profile.d/maude-path.sh ] && . /etc/profile.d/maude-path.sh\n' \
    >> /home/maude/.bashrc \
    && printf '\n# maude: enforce correct PATH order\n[ -f /etc/profile.d/maude-path.sh ] && . /etc/profile.d/maude-path.sh\n' \
    >> /etc/skel/.bashrc

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 22 3000 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
