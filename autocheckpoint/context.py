"""
context.py — Manages the .autocheckpoint/context.yaml file.

Schema:
  project:
    name: <str>
    created_at: <iso8601>

  intent: <str>               # What are we building and why?

  current_focus: <str>        # What is being actively worked on right now?

  decisions:                  # Architectural / technology decisions made
    - text: <str>
      recorded_at: <iso8601>

  known_constraints:          # Hard limits, gotchas, environment constraints
    - text: <str>
      recorded_at: <iso8601>

  tasks:                      # Outstanding work items
    - text: <str>
      status: todo | done
      recorded_at: <iso8601>

  session_summary: <str>      # Latest session summary (overwritten each time)
"""

from __future__ import annotations

import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CONTEXT_FILE_NAME = "context.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _context_path(project_path: Path) -> Path:
    return project_path / ".autocheckpoint" / CONTEXT_FILE_NAME


def load_context(project_path: Path) -> dict:
    """Load context.yaml, returning a clean skeleton if it doesn't exist."""
    path = _context_path(project_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return _ensure_schema(data)
    return _empty_context(project_path)


def save_context(project_path: Path, data: dict) -> None:
    """Persist context to .autocheckpoint/context.yaml and sync all AI tool files."""
    path = _context_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    try:
        from autocheckpoint.sync import sync_tool_contexts
        sync_tool_contexts(project_path, data)
    except Exception:
        pass


CONFIDENCE_THRESHOLD = 0.65  # minimum confidence to write a detected fact into tool files

def _empty_context(project_path: Path) -> dict:
    return {
        "project": {
            "name": project_path.name,
            "created_at": _now_iso(),
        },
        # Confirmed fields — set by user or high-confidence auto-detection
        "intent": "",
        "current_focus": "",
        "recent_steps": [],        # auto-updated: recent session steps (request → what AI did)
        "decisions": [],
        "known_constraints": [],
        "tasks": [],
        "session_summary": "",
        # Raw AI detections with confidence + sources (never edited by user)
        "_detected": {},
    }


def save_detected(project_path: Path, detected: dict) -> None:
    """
    Persist the latest AI-detected context (with confidence + sources) into
    context.yaml under the _detected key, then merge high-confidence facts
    into the confirmed fields.
    """
    ctx = load_context(project_path)
    ctx["_detected"] = detected

    # Use a lower threshold when the project has no context at all yet
    project_is_blank = not ctx.get("intent") and not ctx.get("decisions") and not ctx.get("tasks")
    threshold = 0.30 if project_is_blank else CONFIDENCE_THRESHOLD

    # intent — only fill if confirmed field is empty
    intent_det = detected.get("intent", {})
    if not ctx.get("intent") and intent_det.get("confidence", 0) >= threshold:
        ctx["intent"] = intent_det.get("value", "")

    # current_focus — always update when detected; no threshold (it changes with every new request)
    focus_det = detected.get("current_focus", {})
    if focus_det.get("value"):
        ctx["current_focus"] = focus_det.get("value", "")

    # recent_steps — always replace with the latest session steps
    steps_det = detected.get("recent_steps", {})
    steps_val = steps_det.get("value", []) if isinstance(steps_det, dict) else []
    if isinstance(steps_val, list) and steps_val:
        ctx["recent_steps"] = steps_val

    # decisions — merge new high-confidence ones
    existing_dec = {d["text"].lower() for d in ctx.get("decisions", [])}
    for item in detected.get("decisions", []):
        if item.get("confidence", 0) >= threshold:
            val = item.get("value", "").strip()
            if val and val.lower() not in existing_dec:
                ctx["decisions"].append({"text": val, "recorded_at": _now_iso()})
                existing_dec.add(val.lower())

    # known_constraints — merge new high-confidence ones
    existing_con = {c["text"].lower() for c in ctx.get("known_constraints", [])}
    for item in detected.get("known_constraints", []):
        if item.get("confidence", 0) >= threshold:
            val = item.get("value", "").strip()
            if val and val.lower() not in existing_con:
                ctx["known_constraints"].append({"text": val, "recorded_at": _now_iso()})
                existing_con.add(val.lower())

    # tasks — merge new high-confidence ones
    existing_tasks = {t["text"].lower() for t in ctx.get("tasks", [])}
    for item in detected.get("tasks", []):
        if item.get("confidence", 0) >= threshold:
            val = item.get("value", "").strip()
            if val and val.lower() not in existing_tasks:
                ctx["tasks"].append({"text": val, "status": "todo", "recorded_at": _now_iso()})
                existing_tasks.add(val.lower())

    save_context(project_path, ctx)


def _ensure_schema(data: dict) -> dict:
    """
    Ensure all required keys exist. Also migrates old schema fields:
    - reasoning -> dropped
    - session_summaries (list) -> session_summary (string, last entry)
    """
    # Migrate: session_summaries (old) -> session_summary (new)
    if "session_summaries" in data and "session_summary" not in data:
        old = data.pop("session_summaries", [])
        if old:
            data["session_summary"] = old[-1].get("summary", "") if isinstance(old[-1], dict) else str(old[-1])
        else:
            data["session_summary"] = ""
    # Drop reasoning — too noisy
    data.pop("reasoning", None)

    defaults = {
        "project": {},
        "intent": "",
        "current_focus": "",
        "recent_steps": [],
        "decisions": [],
        "known_constraints": [],
        "tasks": [],
        "session_summary": "",
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
    return data


# ── Mutation helpers ─────────────────────────────────────────────────────────


def set_intent(project_path: Path, intent: str) -> None:
    ctx = load_context(project_path)
    ctx["intent"] = intent.strip()
    save_context(project_path, ctx)


def set_current_focus(project_path: Path, focus: str) -> None:
    ctx = load_context(project_path)
    ctx["current_focus"] = focus.strip()
    save_context(project_path, ctx)


def add_decision(project_path: Path, text: str) -> None:
    ctx = load_context(project_path)
    ctx["decisions"].append({"text": text.strip(), "recorded_at": _now_iso()})
    save_context(project_path, ctx)


def add_constraint(project_path: Path, text: str) -> None:
    ctx = load_context(project_path)
    ctx["known_constraints"].append({"text": text.strip(), "recorded_at": _now_iso()})
    save_context(project_path, ctx)


def add_task(project_path: Path, text: str) -> None:
    ctx = load_context(project_path)
    ctx["tasks"].append({"text": text.strip(), "status": "todo", "recorded_at": _now_iso()})
    save_context(project_path, ctx)


def complete_task(project_path: Path, index: int) -> Optional[str]:
    """Mark a task (1-indexed) as done. Returns the task text or None if not found."""
    ctx = load_context(project_path)
    tasks = ctx.get("tasks", [])
    if 0 < index <= len(tasks):
        tasks[index - 1]["status"] = "done"
        save_context(project_path, ctx)
        return tasks[index - 1]["text"]
    return None


def set_session_summary(project_path: Path, summary: str) -> None:
    """Overwrite the session summary with the latest one."""
    ctx = load_context(project_path)
    ctx["session_summary"] = summary.strip()
    save_context(project_path, ctx)
