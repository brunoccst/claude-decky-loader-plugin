# Claude Sessions for Decky Loader

A [Decky Loader](https://decky.xyz) plugin that lets you manage your
[Claude Code](https://docs.claude.com/en/docs/claude-code) sessions from Steam
Big Picture / Steam Deck Game Mode.

- **Browse sessions** from the Quick Access Menu: every Claude Code session on
  the device, newest first, with its title, project folder and last activity.
- **Read a session** in a full-screen view that's navigable with the D-pad:
  your prompts, Claude's replies and a compact line per tool call.
- **Continue a session** by typing a prompt (the Steam on-screen keyboard
  opens). Claude's reply streams in live, and you can stop it.
- **Start a new session** in any folder, picked from recent projects or with
  the folder browser.

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

To package and deploy to a Deck, use the [Decky CLI](https://github.com/SteamDeckHomebrew/cli)
(`decky plugin build .`) or the VS Code tasks in `.vscode/` (set your Deck's
address in `.vscode/settings.json`, copied from `defsettings.json`).

Scaffolded from the official
[decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template).

## License

BSD-3-Clause, see [LICENSE](LICENSE).
