# maude — Docker image
# Ubuntu 26.04 sandbox appliance for agentic coding.
# Note: appmotel on Docker does NOT use Traefik (no port 80/443 binding);
# it listens on localhost only. Port mapping is handled by the Docker host.
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
    net-tools dnsutils iputils-ping iproute2 \
    ripgrep fd-find \
    unzip zip xz-utils \
    libssl-dev libffi-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── SSH setup ─────────────────────────────────────────────────────────────────
RUN mkdir -p /run/sshd \
    && ssh-keygen -A \
    && sed -i 's/^#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo 'MaxAuthTries 5' >> /etc/ssh/sshd_config \
    && echo 'LoginGraceTime 30' >> /etc/ssh/sshd_config

# ── maude scripts ─────────────────────────────────────────────────────────────
COPY scripts/profile.d/maude-path.sh /etc/profile.d/maude-path.sh
COPY scripts/profile.d/maude-firstlogin.sh /etc/profile.d/maude-firstlogin.sh
COPY scripts/new-user-login.sh /etc/maude/new-user-login.sh
COPY scripts/maude-setup /usr/local/bin/maude-setup
COPY scripts/maude-adduser /usr/local/bin/maude-adduser

RUN chmod 644 /etc/profile.d/maude-*.sh \
    && chmod 755 /etc/maude/new-user-login.sh \
    && chmod 755 /usr/local/bin/maude-setup \
    && chmod 755 /usr/local/bin/maude-adduser \
    && mkdir -p /etc/maude

# ── Default maude user ────────────────────────────────────────────────────────
RUN useradd --create-home --shell /bin/bash --comment "maude default user" maude \
    && echo 'maude:maude' | chpasswd \
    && echo 'maude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/maude \
    && chmod 440 /etc/sudoers.d/maude

# ── mom package manager ───────────────────────────────────────────────────────
RUN groupadd --system mom \
    && usermod -aG mom maude \
    && mkdir -p /etc/mom \
    && curl -fsSL https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-amd64 \
       -o /usr/local/bin/mom \
    && chmod 4755 /usr/local/bin/mom \
    && printf 'group = "mom"\ndeny_list = "/etc/mom/deny.list"\nlog_file = "/var/log/mom.log"\n' \
       > /etc/mom/mom.conf \
    && touch /etc/mom/deny.list

# ── appmotel ──────────────────────────────────────────────────────────────────
RUN curl -fsSL https://raw.githubusercontent.com/dirkpetersen/appmotel/main/install.sh \
    -o /tmp/appmotel-install.sh \
    && chmod +x /tmp/appmotel-install.sh \
    && /tmp/appmotel-install.sh \
    && rm -f /tmp/appmotel-install.sh

# ── web-term (deployed via appmotel) ─────────────────────────────────────────
RUN sudo -u appmotel /home/appmotel/.local/bin/appmo add \
    web-term \
    https://github.com/dirkpetersen/web-term \
    main \
    || echo "WARNING: web-term pre-deploy failed; will retry at runtime"

# ── Docker: Traefik binds localhost only ─────────────────────────────────────
RUN _cfg=/home/appmotel/.config/traefik/traefik.yaml; \
    if [ -f "${_cfg}" ]; then \
      sed -i \
        -e 's/address: ":80"/address: "127.0.0.1:80"/' \
        -e 's/address: ":443"/address: "127.0.0.1:443"/' \
        "${_cfg}"; \
    fi

# ── Write maude.conf ──────────────────────────────────────────────────────────
RUN echo 'MAUDE_DEPLOY_TARGET=docker' > /etc/maude/maude.conf \
    && echo 'MAUDE_BASE_DOMAIN=localhost' >> /etc/maude/maude.conf

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 22 3000 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
