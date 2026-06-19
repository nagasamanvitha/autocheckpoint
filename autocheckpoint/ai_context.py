"""
ai_context.py — Uses Gemini API to synthesize project context from raw signals.

Requires GEMINI_API_KEY environment variable.
Falls back to heuristic extraction if no API key is set.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from autocheckpoint.scanner import gather_all_signals, build_prompt


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def _get_api_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _call_gemini(prompt: str, api_key: str) -> Optional[str]:
    """Call Gemini REST API and return the text response."""
    try:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024,
            }
        }).encode("utf-8")

        url = f"{GEMINI_API_URL}?key={api_key}"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
    except Exception as e:
        return None
    return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract JSON from the model response (strip markdown fences if present)."""
    if not text:
        return None
    # Strip ```json ... ``` or ``` ... ```
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object inside the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def _clean_session_message(text: str) -> str:
    """Delegate to scanner's path-strip helper (same logic, one place)."""
    from autocheckpoint.scanner import _strip_path_prefix
    return _strip_path_prefix(text)


def _clean_assistant_message(text: str) -> str:
    """
    Trim an assistant message to the first meaningful sentence of what was DONE.
    Strips markdown links, code fences, and long explanations.
    """
    # Remove markdown links: [label](url) → label
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Strip backtick markers but keep the content inside them
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Remove bold/italic markers
    text = re.sub(r'\*+', '', text)
    text = text.strip()
    # Take only the first sentence
    for sep in ('. ', '! ', '? ', '\n'):
        idx = text.find(sep)
        if idx > 20:
            text = text[:idx + 1]
            break
    return text.strip()[:120]


def _extract_recent_steps(session_text: str) -> list:
    """
    Build a list of recent session steps from session_text.
    Each step = what the user asked + what the AI did.
    session_text is most-recent-first (recent section only).
    Returns list of up to 8 step strings, most-recent first.
    """
    steps = []
    lines = [
        ln for ln in session_text.split("---history---", 1)[0].splitlines()
        if ln.startswith(("[user]", "[assistant]"))
    ]

    i = 0
    while i < len(lines) and len(steps) < 8:
        if lines[i].startswith("[user]"):
            user_text = _clean_session_message(lines[i][6:].strip())
            if not user_text or len(user_text) < 5:
                i += 1
                continue
            # Find the NEXT assistant line (the response to this user message)
            asst_text = ""
            j = i + 1
            while j < len(lines):
                if lines[j].startswith("[assistant]"):
                    asst_text = _clean_assistant_message(lines[j][11:].strip())
                    break
                if lines[j].startswith("[user]"):
                    break
                j += 1
            step = f"{user_text}"
            if asst_text:
                step += f"  ->  {asst_text}"
            steps.append(step[:200])
        i += 1

    return steps


def _heuristic_fallback(signals: dict, project_name: str) -> dict:
    """
    If no API key is available, extract context from signals without AI.
    Priority: README → package.json description → Claude sessions → source code → git log.
    """
    intent = ""
    source = ""

    # 1. README
    readme = signals.get("readme", "")
    if readme:
        for line in readme.splitlines():
            clean = line.strip().lstrip("#").strip()
            if clean and len(clean) > 10:
                intent = clean[:120]
                source = "README.md"
                break

    # 2. Package manifest description
    if not intent:
        manifest = signals.get("package_manifest", "")
        desc_match = re.search(r"description[=:]\s*['\"]?([^'\"\n]+)", manifest, re.IGNORECASE)
        if desc_match:
            intent = desc_match.group(1).strip()[:120]
            source = "package manifest"

    # 3. Claude Code session history — look for user messages that describe what they're building.
    # session_text layout: recent messages first, then "---history---", then oldest messages.
    # For intent/goal we want the OLDEST matching message (original ask), so read the history section.
    if not intent:
        session_text = signals.get("claude_sessions", "")
        # Split on the history boundary — oldest messages are after it
        if "---history---" in session_text:
            history_section = session_text.split("---history---", 1)[1]
        else:
            history_section = session_text  # fallback: treat all as history
        for line in history_section.splitlines():
            if line.startswith("[user]"):
                content = _clean_session_message(line[6:].strip())
                if any(kw in content.lower() for kw in ("build", "create", "make", "implement", "write", "add", "fix", "i want", "i need", "help me")):
                    if len(content) > 15:
                        intent = content[:120]
                        source = "Claude Code session"
                        break

    # 4. Source code — extract class/function names to infer purpose
    if not intent:
        code = signals.get("source_code", "")
        names = []
        for line in code.splitlines():
            stripped = line.strip()
            m = re.match(r"(?:class|def|function|func|pub fn|fn)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
            if m:
                name = m.group(1)
                if name not in ("main", "test", "setup", "__init__"):
                    names.append(name)
            if len(names) >= 8:
                break
        if names:
            intent = f"{project_name}: {', '.join(names[:5])}"
            source = "source code structure"

    # 5. Git log — most recent commit message (only if git_log signal is non-empty,
    #    meaning scan_git_log confirmed a local .git exists for this project)
    if not intent:
        git = signals.get("git_log", "")
        first_line = git.splitlines()[0].strip() if git.strip() else ""
        if first_line:
            parts = first_line.split(" ", 1)
            msg = parts[1] if len(parts) > 1 else first_line
            intent = msg[:120]
            source = "git log"

    # Current focus — recent section is most-recent-first, so the FIRST user line is the latest request.
    # Stop at the history boundary.
    current_focus = ""
    session_text = signals.get("claude_sessions", "")
    if session_text:
        recent_section = session_text.split("---history---", 1)[0] if "---history---" in session_text else session_text
        for line in recent_section.splitlines():
            if line.startswith("[user]"):
                content = _clean_session_message(line[6:].strip())
                if len(content) > 10:
                    current_focus = content[:120]
                    break

    # Tasks from TODO comments
    tasks = []
    for line in signals.get("todo_comments", "").splitlines():
        match = re.search(r"(?:TODO|FIXME)[:\s]+(.+)", line, re.IGNORECASE)
        if match:
            tasks.append(match.group(1).strip()[:80])
        if len(tasks) >= 5:
            break

    # Recent session steps: what was asked + what AI did
    recent_steps = _extract_recent_steps(signals.get("claude_sessions", ""))

    # Confidence: higher if we found a real signal, lower if just project name
    confidence = 0.55 if source else 0.30

    return {
        "intent": intent or project_name,
        "intent_source": source or "project name",
        "current_focus": current_focus,
        "recent_steps": recent_steps,
        "decisions": [],
        "known_constraints": [],
        "tasks": tasks,
        "_confidence": confidence,
    }


def _normalize_field(raw) -> dict:
    """
    Normalize a field that may be a plain string (old format) or a
    {value, confidence, sources} object (new format).
    Returns a dict with value, confidence, sources always present.
    """
    if isinstance(raw, dict):
        return {
            "value":      str(raw.get("value", "")).strip(),
            "confidence": float(raw.get("confidence", 0.5)),
            "sources":    [str(s) for s in raw.get("sources", []) if str(s).strip()],
        }
    # Plain string — legacy or model didn't follow instructions
    return {"value": str(raw).strip(), "confidence": 0.5, "sources": []}


def _normalize_list_field(raw_list) -> list:
    """Normalize a list of items that may be strings or {value, confidence, sources} dicts."""
    out = []
    for item in (raw_list or []):
        n = _normalize_field(item)
        if n["value"]:
            out.append(n)
    return out


def auto_detect_context(project_path: Path) -> dict:
    """
    Main entry point: gather signals, call Gemini, return structured context dict.

    Each top-level field is now a dict with:
      value      — the detected string
      confidence — float 0.0-1.0
      sources    — list of strings describing where the fact came from

    List fields (decisions, known_constraints, tasks) are lists of those dicts.
    Falls back to heuristic extraction if no API key or call fails.
    """
    signals = gather_all_signals(project_path)
    api_key = _get_api_key()

    if api_key:
        prompt = build_prompt(project_path, signals)
        raw_response = _call_gemini(prompt, api_key)
        parsed = _parse_json_response(raw_response)
        if parsed:
            return {
                "intent":            _normalize_field(parsed.get("intent", "")),
                "current_focus":     _normalize_field(parsed.get("current_focus", "")),
                "decisions":         _normalize_list_field(parsed.get("decisions", [])),
                "known_constraints": _normalize_list_field(parsed.get("known_constraints", [])),
                "tasks":             _normalize_list_field(parsed.get("tasks", [])),
            }

    # Fallback: heuristic extraction
    fallback = _heuristic_fallback(signals, project_path.name)
    conf = fallback.get("_confidence", 0.5)
    intent_src = fallback.get("intent_source", "heuristic")
    return {
        "intent":            {"value": fallback["intent"],        "confidence": conf, "sources": [intent_src]},
        "current_focus":     {"value": fallback["current_focus"], "confidence": conf, "sources": ["Claude Code session" if fallback["current_focus"] else "heuristic"]},
        "recent_steps":      {"value": fallback.get("recent_steps", []), "confidence": conf, "sources": ["Claude Code session"]},
        "decisions":         [{"value": d, "confidence": conf, "sources": ["heuristic"]} for d in fallback["decisions"]],
        "known_constraints": [{"value": c, "confidence": conf, "sources": ["heuristic"]} for c in fallback["known_constraints"]],
        "tasks":             [{"value": t, "confidence": conf, "sources": ["TODO comment"]} for t in fallback["tasks"]],
    }
