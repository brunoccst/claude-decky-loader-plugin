# Claude Sessions for Decky Loader

A [Decky Loader](https://decky.xyz) plugin that lets you manage your
[Claude Code](https://docs.claude.com/en/docs/claude-code) sessions from Steam
Big Picture / Steam Deck Game Mode.

- **Browse projects and sessions** from the Quick Access Menu: the folders
  you've used Claude Code in, each with its sessions, plus the most recent
  sessions across all projects. Start a new session in a project from there.
- **Read a session** in a full-screen view that's navigable with the D-pad
  (Back or B returns to the panel):
  your prompts, Claude's replies and a compact line per tool call.
- **Continue a session** by typing a prompt (the Steam on-screen keyboard
  opens). Claude's reply streams in live, and you can stop it.
- **Start a new session** in any folder, picked from recent projects or with
  the folder browser.
- **Remote Control**: continue a session from claude.ai/code or the Claude app
  on any device (laptop, phone, tablet), or run the Remote Control server so
  you can start new sessions on the Deck from them. Both keep running in Game
  Mode.

Unofficial; not affiliated with Anthropic or Valve.

## How it works

The plugin talks to the Claude Code CLI installed on the same machine, so it
uses your own login (Pro/Max plan or API key) and the same sessions you see
in `claude --resume` on the desktop.

- Sessions are read from Claude Code's transcript store,
  `~/.claude/projects/<project>/<session-id>.jsonl` (or `$CLAUDE_CONFIG_DIR`).
- Prompts run `claude -p <prompt> --resume <session-id> --output-format stream-json`
  in the session's folder; the Python backend streams the events to the UI.
  New sessions omit `--resume` and run in the folder you chose.

Because prompts run unattended, tools that need approval are refused under
Claude Code's default permission mode. Pick a different **Permission mode** in
the plugin settings (for example *Accept edits*) if you want Claude to change
files from the couch. *Bypass all permissions* lets Claude run any command
without asking; only use it in folders you're happy for it to touch.

## Remote Control

[Remote Control](https://code.claude.com/docs/en/remote-control) connects a
Claude Code process on the Deck to the Claude app and claude.ai/code. Started
from a Desktop Mode terminal, that process stops when you switch to Game Mode,
because the desktop session ends. The plugin runs it from Decky's backend
instead, which keeps running across mode switches.

- **Start Remote Session** on a session page runs
  `claude --remote-control <title> --resume <id>` in the session's folder.
  The session then shows up in the Claude app under its title. Prompts from
  the Deck are paused for that session until you stop Remote Control.
- **Start sessions from other devices** in the panel runs `claude remote-control`
  (server mode) in your default folder. It starts again when Decky loads,
  until you turn it off.

Both use the permission mode and model from the plugin settings. The first
time, Claude Code asks `Enable Remote Control? (y/n)`; the plugin shows the
question with Yes and No buttons. Remote Control needs a claude.ai login on a
Pro, Max, Team or Enterprise plan; API keys don't work. Your home folder is
never saved as trusted, so pick a project folder as the default folder for the
server, or expect to confirm the trust question each time.

## Requirements

1. Decky Loader installed.
2. Claude Code installed for the `deck` user and signed in once, from Desktop
   Mode's terminal:

   ```sh
   curl -fsSL https://claude.ai/install.sh | bash
   claude   # sign in, then exit
   ```

   The plugin looks for the CLI in `~/.local/bin`, `~/.claude/local`,
   `~/.npm-global/bin`, `/usr/local/bin` and `PATH`; you can also set the path
   in the plugin settings.

## Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Claude Code CLI path | auto-detect | Full path to the `claude` binary. |
| Permission mode | Claude Code default | Passed as `--permission-mode`. |
| Model | Claude Code default | Passed as `--model`, e.g. `opus` or `sonnet`. |
| Default folder | home folder | Preselected folder for new sessions. |

## Updates

The plugin checks this repo's GitHub releases when it starts and has a
**Check for updates** button at the bottom of its panel. Updates install
through Decky's own installer, which asks you to confirm, and are verified
against the release asset's sha256 digest. This only works while the
repository is public, because Decky downloads without signing in to GitHub.

To publish a release, bump `version` in `package.json`, then run the
**Release** workflow from the Actions tab with a matching tag such as
`v0.2.0` (a hyphen marks a pre-release), or push that tag.

## Development

```sh
pnpm install
pnpm build          # frontend bundle -> dist/index.js
pnpm test           # backend unit tests (Python 3.11+, stdlib only)
```

Layout:

- `main.py`: Decky backend entry point; exposes the RPC methods the UI calls.
- `py_modules/claude_sessions.py`: transcript parsing and the CLI job runner
  (no Decky imports, so it is testable anywhere).
- `src/index.tsx`: Quick Access Menu panel and settings.
- `src/SessionPage.tsx`: full-screen session view at `/claude-sessions/:id`.
- `py_modules/remote_control.py` and `src/components/RemoteControl.tsx`:
  Remote Control processes, run in a pseudo-terminal.

To package and deploy to a Deck, use the [Decky CLI](https://github.com/SteamDeckHomebrew/cli)
(`decky plugin build .`) or the VS Code tasks in `.vscode/` (set your Deck's
address in `.vscode/settings.json`, copied from `defsettings.json`).

Scaffolded from the official
[decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template).

## License

BSD-3-Clause, see [LICENSE](LICENSE).
