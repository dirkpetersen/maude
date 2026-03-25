# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (main) | ✅ |
| older tags | ⚠️ Best-effort |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately via GitHub's [Security Advisories](https://github.com/dirkpetersen/maude/security/advisories/new) or email the maintainer directly. You will receive a response within 72 hours. Confirmed vulnerabilities will be patched and disclosed publicly after a fix is available.

---

## Security Architecture & Threat Model

### Deployment Context

maude is a **multi-user, networked appliance** intended for trusted development environments. It is **not** designed as a hardened public-internet server. Threat actors considered:

- **Malicious co-users** on the same appliance (horizontal privilege escalation)
- **Remote attackers** with network access to the appliance
- **Malicious input** via web-term (terminal injection, path traversal)
- **Supply chain** threats via third-party install scripts

---

## Attack Surface Analysis

### 1. web-term (Browser Terminal)

**Exposure:** HTTP port (default: 3000, routed via Traefik). Accessible to any user who can reach the host.

| Threat | Risk | Mitigation |
|--------|------|-----------|
| Credential brute-force | Medium | SSH `MaxAuthTries 5`, `LoginGraceTime 30`; fail2ban monitors `/var/log/auth.log` |
| Session hijacking | Medium | UUIDs generated per-session; HTTPS recommended for production (see Traefik/appmotel config) |
| Terminal input injection (ANSI escape sequences) | Low-Medium | xterm.js handles terminal emulation; server-side shell is the user's own session |
| Path traversal via file manager | Medium | All file ops use SFTP over SSH; permissions enforced by Linux kernel |
| Username enumeration via login errors | Low | SSH auth errors are generic; timing differences are inherent to SSH protocol |
| DoS via open connections | Medium | Traefik rate limiting (100 req/s average, 50 burst) per app |

**Hardening recommendations:**
- Enable HTTPS (configure `BASE_DOMAIN` + `LETSENCRYPT_EMAIL` via `maude-setup`)
- Restrict web-term to VPN or known IP ranges using Traefik middleware
- Monitor `/var/log/auth.log` and Traefik access logs

---

### 2. appmotel / Traefik

**Exposure:** Traefik listens on port 80 (HTTP) and optionally 443 (HTTPS). On WSL, bound to `127.0.0.1` only.

| Threat | Risk | Mitigation |
|--------|------|-----------|
| App-to-app lateral movement | Low | Each app runs as `appmotel` user; apps cannot write to each other's data dirs |
| Port scanning / exposure | Medium | UFW allows only 22, 80, 443 on VM deployments |
| Traefik dashboard exposure | Low | Dashboard not enabled by default |
| Malicious app deployment | Medium | Only `apps` user (sudoers) can deploy; no anonymous deployment |
| HTTP (no TLS) credential interception | High on untrusted networks | Run behind a TLS terminator or enable Let's Encrypt |
| WSL host-as-server abuse | High (prevented) | Traefik binds `127.0.0.1` on WSL; cannot accept external connections |

**WSL localhost restriction rationale:**
When running under WSL, the Linux network stack shares the Windows host's interfaces. Binding Traefik to `127.0.0.1` prevents the developer's laptop from acting as a public hosting platform, which would expose apps to the internet without the user's knowledge.

---

### 3. mom (setuid package manager)

**Exposure:** `/usr/local/bin/mom` is a setuid-root binary. Any member of the `mom` group can install/update packages.

| Threat | Risk | Mitigation |
|--------|------|-----------|
| Privilege escalation via malicious package | High | `mom` only calls `apt-get`/`dnf` with hardcoded absolute paths; no shell execution |
| Deny list bypass | Low | Deny list uses glob matching; all package names validated against strict regex before processing |
| Group membership abuse | Medium | Only explicitly provisioned users added to `mom` group (via `maude-adduser`) |
| Environment injection (LD_PRELOAD, PATH) | Low | `mom` strips entire caller environment; only safe vars (PATH, LANG, proxy) passed to subprocess |
| Audit log tampering | Low | `/var/log/mom.log` writable only by root; syslog copy provides independent audit trail |

**Hardening recommendations:**
- Audit `mom` group membership regularly: `getent group mom`
- Review `/var/log/mom.log` and `/etc/mom/deny.list` periodically
- Consider restricting deny list to match your threat model (e.g., block `*-dev`, compiler tools)

---

### 4. SSH Daemon

**Exposure:** Port 22, accessible per firewall rules.

| Threat | Risk | Mitigation |
|--------|------|-----------|
| Root login | Prevented | `PermitRootLogin no` enforced in sshd_config |
| Brute-force | Medium | `MaxAuthTries 5`, `LoginGraceTime 30`, fail2ban |
| Key-based auth not default | Medium | Password auth enabled for usability; users should add `~/.ssh/authorized_keys` |
| Port 22 exposure | Medium | UFW restricts to allowed sources; consider changing to a non-standard port for internet-facing VMs |

---

### 5. First-Boot Script (curl-pipe pattern)

**Risk:** `first-boot.sh` downloads and executes scripts from GitHub at first boot (`appmotel/install.sh`).

| Threat | Risk | Mitigation |
|--------|------|-----------|
| MITM / DNS hijack during install | Medium | `curl -fsSL` enforces TLS; GitHub's TLS cert is pinned by OS trust store |
| Supply chain compromise of upstream repos | Medium | Pin to a specific commit SHA instead of `main` for production builds |
| No integrity verification of downloaded scripts | Medium | **Recommended:** add SHA256 verification of downloaded install.sh |

**Recommendation for hardened deployments:**
```bash
# Pin to a specific commit instead of 'main':
curl -fsSL https://raw.githubusercontent.com/dirkpetersen/appmotel/<COMMIT_SHA>/install.sh \
  -o /tmp/appmotel-install.sh
echo "<expected-sha256>  /tmp/appmotel-install.sh" | sha256sum -c
```

---

### 6. Claude Code Installation (User-Initiated)

**Risk:** Users are prompted to install Claude Code via `curl | bash`.

| Threat | Risk | Mitigation |
|--------|------|-----------|
| Malicious Claude Code binary | Low | Official Anthropic install script served over TLS |
| API key exposure in shell history | Medium | Advise users to set `ANTHROPIC_API_KEY` in `~/.config/maude/` rather than shell history |
| Claude Code executing malicious prompts | Varies | User responsibility; standard AI safety guidance applies |

---

### 7. Multi-User Isolation

Users are **separate Linux users** with standard DAC (discretionary access control).

| Capability | Isolated? |
|-----------|-----------|
| Home directory | ✅ Yes (`chmod 700` default) |
| Processes | ❌ No (`ps aux` visible to all; by design) |
| Deployed apps (appmotel) | ❌ Apps visible to all; data in `/home/appmotel/` |
| Package installation | ✅ Only via `mom` (group-gated) |
| Root escalation | ✅ No sudo for regular users |

**Recommendation:** For stronger isolation, consider running appmotel with per-user instances (future roadmap) or using Linux namespaces.

---

## Hardening Checklist

After deploying maude, consider the following:

- [ ] Run `sudo maude-setup` to configure domain + Let's Encrypt (enables HTTPS)
- [ ] Set a strong password for the `maude` default user or disable password auth
- [ ] Add SSH public keys to `~/.ssh/authorized_keys` and disable `PasswordAuthentication`
- [ ] Review `/etc/mom/deny.list` and add packages inappropriate for your environment
- [ ] For internet-facing VMs: restrict UFW to known source IPs where possible
- [ ] Enable fail2ban email alerts (edit `/etc/fail2ban/jail.local`)
- [ ] Rotate the default `maude` password before sharing the appliance
- [ ] Pin appmotel and web-term to specific release tags (edit `first-boot.sh`)
- [ ] Review users in `mom` group: `getent group mom`
- [ ] Monitor logs: `/var/log/auth.log`, `/var/log/mom.log`, Traefik access logs

---

## Known Limitations (Accepted Risks for v0.x)

1. **HTTP-only by default** — TLS not configured until `maude-setup` is run. Do not transmit sensitive credentials over HTTP on untrusted networks.
2. **No Azure AD authentication** — web-term uses SSH password auth. Azure AD integration is on the roadmap.
3. **No per-user appmotel instances** — all user apps run under the shared `appmotel` user.
4. **Process visibility** — users can see each other's processes (`ps aux`). This is intentional for a collaborative dev environment.
5. **curl-pipe installs** — first-boot installs appmotel via `curl | bash`. Acceptable for dev environments; pin to commit SHA for production.
