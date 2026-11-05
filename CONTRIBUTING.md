# Contributing to Sleepy Proton Drive

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

[Open an issue](https://github.com/TheSleepyLabs/sleepy-proton-drive/issues) with:

- your Linux distro and version, and `rclone version`
- what you did, what you expected, and what happened
- the output of `sleepy-proton-check` and the end of the log (`~/.local/share/proton-sync.log`)

**Remove your email address, folder names you consider private and anything password-like
before posting.** Security problems go through [SECURITY.md](SECURITY.md) instead.

## Project layout

| Path | What |
|---|---|
| `bin/sleepy-proton-sync` | The sync engine (Bash). Everything else drives this. |
| `bin/sleepy-proton-wizard` | Terminal setup wizard (Bash). |
| `bin/sleepy-proton-gui` | The app (Python + PySide6). |
| `check-requirements.sh` | Requirement checker / fixer (installed as `sleepy-proton-check`). |
| `install.sh`, `uninstall.sh` | Install into `~/.local` (no root). |
| `config/proton-sync.conf.example` | Example settings file. |
| `tests/smoke-test.sh` | Tests the engine against a fake local remote. |

## Making a change

```bash
git clone https://github.com/<your-username>/sleepy-proton-drive.git
cd sleepy-proton-drive
git checkout -b my-change
```

Run the tools straight from the checkout while you work (`./bin/sleepy-proton-gui`,
`./bin/sleepy-proton-sync --dry-run`). Before opening a pull request, run the same checks as CI:

```bash
# Engine tests: no Proton account needed, nothing real is touched
bash tests/smoke-test.sh

# Lint
shellcheck --severity=warning -e SC1090,SC1091,SC2034 \
  check-requirements.sh install.sh uninstall.sh tests/smoke-test.sh \
  bin/sleepy-proton-sync bin/sleepy-proton-wizard
python3 -m py_compile bin/sleepy-proton-gui
desktop-file-validate assets/sleepy-proton-drive.desktop
```

Guidelines:

- **Never risk people's files.** Anything that can delete (mirror/two-way modes) needs a safety
  check and a test in `tests/smoke-test.sh`.
- **Never put secrets on a command line**: pass passwords through `rclone obscure -` (stdin).
- Keep it working on any Linux distro: no distro-specific commands without a fallback, and the
  terminal tools must work without Python.
- Keep the wording plain. Most people using this aren't developers.
- If you change behaviour, update `README.md` and `CHANGELOG.md`.

## Design

The app uses The Sleepy Labs dark theme. Please reuse the colours in `SLEEPY_STYLESHEET` in
`bin/sleepy-proton-gui` rather than adding new ones:

| Role | Colour |
|---|---|
| Background | `#07050d` |
| Surface / cards | `#141124` / `#1e1938` |
| Primary accent (violet) | `#8b5cf6` |
| Action (cyan) | `#38bdf8` |
| Secondary (lavender) | `#b794ff` |
| Danger (pink) | `#ec4899` |
| Success (green) | `#10b981` |

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
