# AGENTS

## Push to Kindle iPhone Shortcut lessons

- Keep Shortcuts dumb: collect URL, then call the Mac mini wrapper. Real logic belongs in repo scripts.
- For iOS **Run Script over SSH**, pass the URL as a quoted argument; do not rely on stdin/`read`, which can hang.
- Quote URL variables, especially `x.com/...?...` URLs. `zsh` may glob unquoted query strings and fail with `no matches found`.
- Prefer SSH key auth from Shortcuts over password auth. Add the full public key line to `~/.ssh/authorized_keys` on the Mac mini.
- Non-interactive SSH has a thin PATH. Wrapper scripts that need Homebrew tools should set:
  ```bash
  export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
  ```
- X/Twitter URLs route through defuddle → EPUB and require `pandoc` on the Mac mini.
- Debug iPhone runs from Mac mini with:
  ```bash
  tail -120 ~/logs/iphone-push-to-kindle.log
  ps aux | grep -E "send_to_kindle|iphone_push" | grep -v grep
  ```
- A Shortcut result of `0` means command exit code success, not proof that Gmail sent mail. Check logs/Gmail after.
