# Windows Setup Guide for maude

Complete guide to installing Windows Terminal, WSL, and importing maude on Windows 10/11.

---

## Step 1 — Install Windows Terminal

Windows Terminal is the recommended way to use maude. It supports multiple tabs, WSL profiles, and proper Unicode rendering.

**Option A — Microsoft Store (recommended):**
1. Open the Microsoft Store app
2. Search for **Windows Terminal**
3. Click **Install**

**Option B — winget (from Command Prompt):**
```cmd
winget install --id Microsoft.WindowsTerminal --source winget
```

**Option C — Direct download:**
Download the latest `.msixbundle` from the [Windows Terminal releases page](https://github.com/microsoft/terminal/releases/latest) and double-click to install.

---

## Step 2 — Enable WSL

Open **Command Prompt as Administrator** (right-click → "Run as administrator"):

```cmd
wsl --install --no-distribution
```

This enables the WSL feature and installs the Linux kernel. **Restart your computer** when prompted.

If WSL is already installed, update it:
```cmd
wsl --update
```

Verify WSL 2 is the default:
```cmd
wsl --set-default-version 2
```

---

## Step 3 — Download and Import maude

Open a regular **Command Prompt** (not PowerShell, not Administrator):

```cmd
curl -L -o maude-wsl.tar.gz https://github.com/dirkpetersen/maude/releases/latest/download/maude-wsl-ubuntu2604-latest.tar.gz
```

> **Note:** If the above URL does not work, go to
> https://github.com/dirkpetersen/maude/releases/latest
> and copy the exact URL for `maude-wsl-ubuntu2604-*.tar.gz`, then:
> ```cmd
> curl -L -o maude-wsl.tar.gz <paste-url-here>
> ```

Create the install directory and import:

```cmd
mkdir C:\maude
wsl --import maude C:\maude maude-wsl.tar.gz --version 2
```

This registers maude as a WSL distribution named **maude**.

---

## Step 4 — Open maude in Windows Terminal

1. Open **Windows Terminal**
2. Click the **▼** (dropdown arrow) next to the `+` tab button
3. You should see **maude** in the list — click it

> If maude does not appear, close and reopen Windows Terminal. It auto-detects
> new WSL distributions on startup.

On first launch, maude will:
- Run the first-boot setup (installs appmotel, deploys web-term)
- Drop you into a shell as the `maude` user
- Prompt once whether you want to install Claude Code

Your prompt will look like:
```
maude@maude:~$
```
(`user@hostname:directory$`)

---

## Step 5 — Fix the Starting Directory (if needed)

If Windows Terminal opens maude in your Windows home directory (`/mnt/c/Users/...`)
instead of `/home/maude`, fix it:

1. In Windows Terminal, click **Settings** (gear icon or `Ctrl+,`)
2. Click **maude** in the left sidebar under "Profiles"
3. Set **Starting directory** to:
   ```
   \\wsl$\maude\home\maude
   ```
4. Click **Save**

---

## Step 6 — Open the Browser Terminal

Once maude's first-boot setup finishes (takes ~1 minute), open your browser:

```
http://localhost:3000
```

Log in with username `maude` and password `maude`.

> Change the password immediately: in the maude shell, run `passwd`

---

## Managing maude

```cmd
REM Start maude
wsl -d maude

REM Stop maude
wsl --terminate maude

REM Check status
wsl --list --verbose

REM Uninstall maude (removes all data)
wsl --unregister maude
```

---

## Troubleshooting

**"The distribution could not be found."**
→ Run `wsl --list` to see registered distros. Re-import if maude is missing.

**Prompt shows Windows path instead of /home/maude**
→ Follow Step 5 above to fix the starting directory.

**Web terminal not loading at localhost:3000**
→ First-boot may still be running. Wait 1-2 minutes and reload.
→ In the maude shell: `sudo journalctl -u maude-first-boot -f`

**maude not showing in Windows Terminal dropdown**
→ Fully close and reopen Windows Terminal (right-click taskbar icon → close).
