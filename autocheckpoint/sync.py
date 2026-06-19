"""
sync.py — Writes autocheckpoint context into every AI tool's native format.

On every context save, this module upserts a fenced block into:
  - CLAUDE.md          (Claude Code / Anthropic)
  - .cursorrules       (Cursor)
  - .windsurfrules     (Windsurf)
  - .ai-context.md     (universal fallback — any tool can reference this)

Each file gets a clearly-marked section that is replaced on every update,
leaving any existing content in that file untouched outside the markers.
"""

from __future__ import annotations

import re
from pathlib import Path

_MARKER_START = "<!-- autocheckpoint:start -->"
_MARKER_END   = "<!-- autocheckpoint:end -->"
_SECTION_RE   = re.compile(
    re.escape(_MARKER_START) + r".*?" + re.escape(_MARKER_END),
    re.DOTALL,
)

# Files written to by default (created if missing)
TOOL_FILES = [
    "CLAUDE.md",
    ".cursorrules",
    ".windsurfrules",
    ".ai-context.md",
]


def _build_block(ctx: dict) -> str:
    """
    Build the context block written to CLAUDE.md / .cursorrules / .windsurfrules.
    No static 'Goal' field — intent is inferred from session history + current focus.
    Any AI tool reading this block can immediately understand what is happening and pick up.
    """
    lines = [_MARKER_START, "## AutoCheckpoint — Session Context", ""]
    lines += [
        "> *Auto-generated. Shows what was worked on and what steps were taken.*",
        "> *Read this to understand the project state before responding.*",
        "",
    ]

    # Current focus — the most important line: what is happening RIGHT NOW
    if ctx.get("current_focus"):
        lines += [f"**Currently working on:** {ctx['current_focus']}", ""]

    # Session history — what was requested and what AI did, most recent first
    recent_steps = ctx.get("recent_steps", [])
    if recent_steps:
        lines += ["**Session History** *(most recent first — use this to understand intent and reasoning)*:"]
        for step in recent_steps[:8]:
            lines.append(f"- {step}")
        lines.append("")

    # Manual session summary (set via `autocheckpoint context add-session`)
    if ctx.get("session_summary"):
        lines += ["**Session Summary:**", ctx["session_summary"], ""]

    # Decisions
    decisions = [
        (d["text"] if isinstance(d, dict) else str(d))
        for d in ctx.get("decisions", [])
    ]
    if decisions:
        lines += ["**Decisions Made:**"] + [f"- {d}" for d in decisions] + [""]

    # Constraints
    constraints = [
        (c["text"] if isinstance(c, dict) else str(c))
        for c in ctx.get("known_constraints", [])
    ]
    if constraints:
        lines += ["**Known Constraints:**"] + [f"- {c}" for c in constraints] + [""]

    # Open tasks
    open_tasks = [t for t in ctx.get("tasks", []) if t.get("status") != "done"]
    if open_tasks:
        lines += ["**Open Tasks:**"]
        for t in open_tasks:
            text = t["text"] if isinstance(t, dict) else str(t)
            lines.append(f"- [ ] {text}")
        lines.append("")

    lines.append(_MARKER_END)
    return "\n".join(lines)


def _upsert(file_path: Path, block: str) -> None:
    """Insert or replace the autocheckpoint section in file_path."""
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        if _SECTION_RE.search(existing):
            updated = _SECTION_RE.sub(block, existing).strip()
        else:
            updated = existing.rstrip() + "\n\n" + block
    else:
        updated = block

    file_path.write_text(updated + "\n", encoding="utf-8")


def sync_tool_contexts(project_path: Path, ctx: dict) -> None:
    """
    Write ctx into every AI tool context file under project_path.
    Skips the sync silently if no meaningful context exists yet.
    """
    has_content = any([
        ctx.get("current_focus"),
        ctx.get("recent_steps"),
        ctx.get("decisions"),
        ctx.get("known_constraints"),
        ctx.get("tasks"),
        ctx.get("session_summary"),
    ])
    if not has_content:
        return

    block = _build_block(ctx)
    for name in TOOL_FILES:
        try:
            _upsert(project_path / name, block)
        except Exception:
            pass
