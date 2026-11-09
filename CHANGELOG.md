# Changelog

All notable changes to **Sleepy Proton Drive** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] - 2026-11-09

### Added
- **Restart Sync** button: stops the running sync and starts a fresh one.
- `sleepy-proton-sync --stop`, `--restart` and `--running` to control a sync from the terminal.
- The app shows when an automatic background sync is running.

### Fixed
- **Stop Sync** only stopped syncs started from the app window. It now stops background
  (timer) syncs too.
- The app wouldn't open when PySide6 was in its private environment (fresh installs).
- A stopped background sync showed as "failed" in systemd.
- Sync Now and Stop Sync looked clickable while they were turned off.

## [1.1.0] - 2026-11-05

Review and hardening release, ready for public use.

### Fixed
- `--mode`, `--dry-run` and `--no-notify` on the command line were overridden by the config file.
- **Stop Sync** didn't stop rclone. It now stops the running transfer straight away.
- Two folders with the same name (e.g. `~/Documents` and `~/Work/Documents`) silently overwrote
  each other in Proton Drive. They're now refused with a warning.
- `bisync` failed on its first run (it needs `--resync`). The baseline is now made automatically.
- Logging in froze the app window. Login and tests now run in the background.
- Passwords with spaces at the start or end were cut short.
- Saving from the app could write a broken config if a path or flag contained quotes or `$`.
- Settings tab values could overwrite what you'd just chosen in the setup wizard.
- The app and the timer could both use two differently named timers at once.
- `&` in labels showed as an underline, and boxes appeared around every label in the app.
- Several commands could fail at random because of a `pipefail` + `grep -q` race.

### Added
- **Safety check:** in `sync`/`bisync` mode an empty folder (e.g. an unplugged drive) is
  skipped instead of wiping its cloud copy.
- Confirmation before a mirror or two-way sync from the app, and before closing during a sync.
- `sleepy-proton-sync --enable-timer INTERVAL`, `--disable-timer`, `--timer-status`, `--version`.
- `sleepy-proton-check --fix` installs rclone and PySide6 on Fedora, Debian/Ubuntu, Arch and
  openSUSE, with a private PySide6 install as a fallback.
- Optional mailbox password and 2FA secret key in both wizards; the terminal wizard can keep
  your existing folders and settings.
- `install.sh --yes`, `--desktop-shortcut`, `--no-deps`; `uninstall.sh --purge`.
- Desktop menu actions: **Sync Now** and **Setup Wizard**.
- `tests/smoke-test.sh` (runs the engine against a fake local remote) and CI for it.
- `SECURITY.md`.

### Changed
- Background sync units are now created by the engine (`sleepy-proton-sync.timer`), so they
  work whatever folder you install to. The old static `systemd/` files are gone, and the
  installer moves an existing timer over automatically.
- Default skip rules now include caches and half-downloaded files, and symlinks aren't followed.
- The installer no longer creates a config file; the setup wizard does that.
- Honest security wording in the docs: rclone obscures, but doesn't encrypt, saved passwords.

## [1.0.0] - 2026-11-05

### Added
- **Interactive Onboarding Wizard**:
  - Full CLI setup wizard (`sleepy-proton-wizard`) with colorized prompts and auto-detection.
  - Graphical PySide6 multi-step wizard (`SetupWizardDialog`) with credentials validation, directory picker, and live rclone testing.
  - Support for 2FA TOTP codes, Two-Password mode (Mailbox password), and optional OTP Secret Key persistence.
- **Configurable Settings Engine**:
  - Customizable remote name, device label (`Devices/<DeviceName>/...`), and sync modes (`copy`, `sync`, `bisync`).
  - Concurrency controls (`--transfers`, `--checkers`), API throttling (`--tpslimit`), and bandwidth limits (`--bwlimit`).
  - Flexible exclusion rules with one-click developer presets (`.git`, `node_modules`, `.venv`, cache files).
- **System Requirements & Pre-Flight Diagnostics**:
  - `check-requirements.sh` validating OS environment, rclone version (>= 1.63.0), protondrive backend provider, Python/PySide6, systemd user session, and Proton API HTTPS reachability.
  - Automated remediation flag `--fix` / `--install-missing`.
- **Graphical Dashboard (`sleepy-proton-gui`)**:
  - Real-time engine log stream, one-click "Sync Now", "Dry-Run Preview", and "Stop Sync".
  - Folder manager with file-manager opening via `xdg-open`.
  - Systemd timer management and journal status viewer directly from the UI.
- **The Sleepy Labs Cosmic Cyber Dark Branding**:
  - Custom SVG application vector icon.
  - XDG desktop entry (`thesleepylabs-proton-drive.desktop`).
- **Open Source Infrastructure**:
  - MIT License.
  - GitHub Actions CI workflow for ShellCheck and Python linting.
  - Automated `install.sh` and `uninstall.sh` lifecycle scripts.
