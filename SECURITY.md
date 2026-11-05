# Security policy

## Reporting a problem

If you find a security problem in Sleepy Proton Drive (for example a way it could leak a
password, delete files it shouldn't, or run unexpected commands), please **don't open a public
issue**. Instead, use GitHub's private
[**Report a vulnerability**](https://github.com/TheSleepyLabs/sleepy-proton-drive/security/advisories/new)
form.

Please include what you found, how to reproduce it, and which version you're using
(`sleepy-proton-sync --version`). You'll get a reply as soon as possible, usually within a week.

Problems in rclone itself or its Proton Drive backend should be reported to
[rclone](https://github.com/rclone/rclone/security). Problems with Proton's own services go to
[Proton](https://proton.me/security/bug-bounty).

## What this tool does with your login

- Your Proton password is passed to rclone in obscured form (never as plain text on the command
  line) and stored by rclone in `~/.config/rclone/rclone.conf`.
- rclone's "obscured" passwords can be turned back into the real password by anyone who can read
  that file. Keep it private (rclone sets it to be readable only by you), or encrypt rclone's
  config with a password (`rclone config`, then `s`).
- Sleepy Proton Drive never sends your data anywhere except to Proton, through rclone.

## Supported versions

Only the latest release gets fixes.
