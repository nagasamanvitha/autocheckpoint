# AutoCheckpoint

**Never lose your code or AI context again. Auto-saves code, decisions, tasks, and session history across Claude Code, Cursor, Windsurf**

```bash
pip install autocheckpoint
```

---

## Why not just Git?

Git stores:
- Code

AutoCheckpoint stores:
- What the AI did
- What decisions were made
- What was skipped
- What should happen next

Git tells you *what* changed. AutoCheckpoint tells you *what happened during the session.*

---

## The killer feature

**Day 1** — Claude Code:
> "Add JWT auth"
> "Use PostgreSQL"
> "Skip refresh tokens for now"

**Day 2** — you open Cursor.

Cursor already knows:

```
Currently working on: JWT auth implementation

Session History:
- Add JWT auth          ->  Done. auth.py created with bcrypt hashing
- Choose database       ->  Done. PostgreSQL selected, schema in db/schema.sql
- Refresh tokens        ->  Skipped. needs testing before adding
```

No re-explaining. No catching up. It just picks up.

This works across **Claude Code, Cursor, Windsurf, Antigravity** — any tool that reads its rules file.

---

## What it actually does

Three things, in order of what matters:

| | What it does |
|---|---|
| **Project Memory** | Reads your Claude Code / Cursor / Antigravity sessions — extracts what was requested, what AI did, what was decided |
| **Cross-Agent Handoff** | Writes that memory into `CLAUDE.md`, `.cursorrules`, `.windsurfrules` — every tool picks it up automatically |
| **Code Snapshots** | Automatically saves your project files in the background so you can restore to any point |

---

## End-to-End Setup

### 1. Install

```bash
pip install autocheckpoint
```

### 2. Go to your project folder

```bash
cd your-project
```

### 3. Initialize

```bash
autocheckpoint init
```

This will ask where to store backups (a local folder on your machine), then scans your project and writes context to `CLAUDE.md`, `.cursorrules`, `.windsurfrules`.

### 4. Start the background watcher

```bash
autocheckpoint start --background
```

That's it. From now on:
- Every file change → snapshot saved automatically in the background
- Every new Claude Code message → context updated automatically
- CLAUDE.md / .cursorrules / .windsurfrules stay current — no commands needed

---

## What gets written to AI tool files

Every AI tool that reads its rules file will see this automatically:

```
## AutoCheckpoint — Session Context

> Auto-generated. Read this to understand the project state before responding.

Currently working on: add fibonacci series to main.py

Session History (most recent first):
- add fibonacci series  ->  Done. main.py now prints Fibonacci series: 0, 1, 1, 2, 3, 5, 8...
- change to even numbers  ->  Done. main.py prints even numbers from 2 to 50
- create hello.py  ->  Created hello.py with Hello World print
- write odd numbers  ->  Created main.py printing odd numbers 1 to 50

Decisions Made:
- (any architectural decisions recorded)

Open Tasks:
- (any open tasks)
```

When you open **Cursor, Windsurf, or Antigravity** on the same project — they read their rules file and immediately understand the full session history. No re-explaining.

---

## Automatic context updates

You never need to run any command for context to stay current:

| Trigger | What updates |
|---|---|
| You send a message in Claude Code | `current_focus` + session history updated automatically |
| You save a file | Full context re-scan, all AI tool files synced |
| You switch to Cursor / Windsurf / Antigravity | Their rules file already has the latest context |

---

## Check status

```bash
autocheckpoint status
```

---

## Restore on a new machine

```bash
cd new-empty-folder
autocheckpoint restore
```

- Select project
- Select snapshot (timestamped list)
- All files restored + CLAUDE.md / .cursorrules / .windsurfrules regenerated

Or to restore the latest snapshot instantly:

```bash
autocheckpoint restore --latest
```

---

## All commands

```bash
# Setup
autocheckpoint init                      # initialize project, set backup location
autocheckpoint start --background        # start background watcher
autocheckpoint stop                      # stop background watcher
autocheckpoint status                    # show current state

# Restore
autocheckpoint restore                   # pick a snapshot to restore
autocheckpoint restore --latest          # restore most recent snapshot instantly

# Context
autocheckpoint context refresh           # force re-scan right now (normally automatic)
autocheckpoint context show              # show current stored context
autocheckpoint context reset             # clear all context and start fresh
autocheckpoint context add-session "..."  # manually record what happened this session

# Handoff
autocheckpoint handoff                   # full project state summary (terminal)
autocheckpoint handoff --markdown        # raw markdown — paste into any AI tool
```

---

## What signals are scanned for context

| Source | What's extracted |
|---|---|
| Claude Code session history | What was requested, what AI did — full step-by-step |
| Antigravity / Cursor session files | Recent conversations |
| Source code | Functions, classes, structure |
| `README.md` | Project description |
| `git log` | Recent changes |
| TODO / FIXME comments | Open tasks |
| `package.json` / `pyproject.toml` / `Cargo.toml` | Project metadata |

---

## Optional: Gemini API key (for smarter context)

Without a key: context is extracted using heuristics (session history + code structure).  
With a key: Gemini reads all signals and gives confidence-scored, AI-extracted context.

```bash
# Windows
set GEMINI_API_KEY=your_key_here

# macOS / Linux
export GEMINI_API_KEY=your_key_here
```

Get a free key at [aistudio.google.com](https://aistudio.google.com).

---

## Files created in your project

```
your-project/
├── .autocheckpoint/
│   ├── autocheckpoint.yaml    ← config (backup path, project name)
│   ├── context.yaml           ← focus, session history, decisions, tasks
│   ├── handoff.md             ← human-readable summary
│   └── watcher.log            ← background watcher logs
├── .autocheckpointignore      ← patterns to exclude from snapshots
├── CLAUDE.md                  ← auto-written (Claude Code reads this)
├── .cursorrules               ← auto-written (Cursor reads this)
├── .windsurfrules             ← auto-written (Windsurf reads this)
├── .ai-context.md             ← auto-written (universal fallback)
└── your code...
```

Backup folder (local):
```
~/autocheckpoint_backups/
└── your-project/
    ├── snapshot_2026-06-19_14-30-00.tar.gz
    ├── snapshot_2026-06-19_14-31-05.tar.gz
    └── ...
```

---

## Ignore patterns

Edit `.autocheckpointignore` (created automatically by `init`):

```gitignore
.git/
node_modules/
venv/
.venv/
__pycache__/
dist/
build/
```

---

## License

MIT
