"""
scanner.py — Gathers raw context signals from a project directory.

Scans:
  - README / CLAUDE.md / .cursorrules / .cursor/ (Cursor AI)
  - package.json, pyproject.toml, setup.py, Cargo.toml, etc.
  - Git commit log (last 30 commits)
  - TODO / FIXME comments across source files
  - Antigravity IDE brain session files (recent conversations)
"""

from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path
from typing import Optional


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_text(path: Path, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text[:max_chars].strip()
    except Exception:
        return ""


def _find_file(project_path: Path, *names) -> Optional[Path]:
    for name in names:
        p = project_path / name
        if p.exists():
            return p
    return None


# ── Individual scanners ───────────────────────────────────────────────────────

def scan_readme(project_path: Path) -> str:
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = project_path / name
        if p.exists():
            return _read_text(p, 3000)
    return ""


def scan_claude_md(project_path: Path) -> str:
    """CLAUDE.md is the canonical AI context file used by Claude/Anthropic projects."""
    for name in ["CLAUDE.md", ".claude/context.md", ".claude/instructions.md"]:
        p = project_path / name
        if p.exists():
            return _read_text(p, 3000)
    return ""


def scan_cursor_rules(project_path: Path) -> str:
    """Cursor AI stores rules in .cursorrules or .cursor/rules."""
    for name in [".cursorrules", ".cursor/rules", ".cursor/rules.md"]:
        p = project_path / name
        if p.exists():
            return _read_text(p, 2000)
    return ""


def scan_cursor_chat(project_path: Path) -> str:
    """Try to read recent Cursor chat history stored in the project."""
    chat_dir = project_path / ".cursor" / "chat"
    if not chat_dir.exists():
        return ""
    snippets = []
    for f in sorted(chat_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            # Cursor stores messages as a list of {role, content}
            msgs = data if isinstance(data, list) else data.get("messages", [])
            for m in msgs[:10]:
                role = m.get("role", "")
                content = str(m.get("content", ""))[:200]
                if content:
                    snippets.append(f"[{role}] {content}")
        except Exception:
            pass
    return "\n".join(snippets)[:2000]


def scan_package_manifest(project_path: Path) -> str:
    """Extract name + description from package.json, pyproject.toml, setup.py, Cargo.toml."""
    parts = []

    # package.json
    pj = project_path / "package.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
            parts.append(f"[package.json] name={d.get('name','')} description={d.get('description','')}")
        except Exception:
            pass

    # pyproject.toml
    pp = project_path / "pyproject.toml"
    if pp.exists():
        text = _read_text(pp, 1000)
        parts.append(f"[pyproject.toml]\n{text}")

    # setup.py
    sp = project_path / "setup.py"
    if sp.exists():
        parts.append(f"[setup.py]\n{_read_text(sp, 800)}")

    # Cargo.toml
    ct = project_path / "Cargo.toml"
    if ct.exists():
        parts.append(f"[Cargo.toml]\n{_read_text(ct, 800)}")

    # go.mod
    gm = project_path / "go.mod"
    if gm.exists():
        parts.append(f"[go.mod]\n{_read_text(gm, 400)}")

    return "\n\n".join(parts)


def scan_git_log(project_path: Path) -> str:
    """Get the last 30 git commit messages — only if this project has its own .git."""
    if not (project_path / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-30"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def scan_todo_comments(project_path: Path) -> str:
    """Scan source files for TODO / FIXME / HACK comments (max 40 lines)."""
    extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cs", ".cpp", ".c"}
    todos = []
    try:
        for root, dirs, files in os.walk(project_path):
            # Skip common non-source dirs
            dirs[:] = [d for d in dirs if d not in {
                ".git", "node_modules", "venv", ".venv", "__pycache__",
                "dist", "build", ".autocheckpoint", ".cursor"
            }]
            for fname in files:
                if Path(fname).suffix.lower() not in extensions:
                    continue
                fpath = Path(root) / fname
                try:
                    lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for i, line in enumerate(lines, 1):
                        upper = line.upper()
                        if "TODO" in upper or "FIXME" in upper or "HACK" in upper:
                            rel = str(fpath.relative_to(project_path))
                            todos.append(f"{rel}:{i}: {line.strip()}")
                            if len(todos) >= 40:
                                break
                except Exception:
                    pass
            if len(todos) >= 40:
                break
    except Exception:
        pass
    return "\n".join(todos)


def scan_antigravity_sessions(project_path: Path) -> str:
    """
    Read recent Antigravity IDE brain session files that mention this project.
    Brain dir: C:\\Users\\HP\\.gemini\\antigravity-ide\\brain\\
    Each conversation dir contains a transcript.jsonl.
    """
    brain_dir = Path.home() / ".gemini" / "antigravity-ide" / "brain"
    if not brain_dir.exists():
        return ""

    project_str = str(project_path).lower().replace("\\", "/")
    snippets = []

    # Find all conversation dirs, sorted by modification time (newest first)
    conv_dirs = sorted(
        [d for d in brain_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )[:10]  # Look at 10 most recent conversations

    for conv_dir in conv_dirs:
        transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if not transcript.exists():
            continue
        try:
            lines = transcript.read_text(encoding="utf-8", errors="ignore").splitlines()
            # Check if this conversation mentions the project path
            combined = "\n".join(lines[:50])  # Check first 50 lines
            if project_str not in combined.lower():
                # Try by project name
                if project_path.name.lower() not in combined.lower():
                    continue
            # Extract USER_INPUT messages for context
            for line in lines:
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "USER_INPUT":
                        content = str(entry.get("content", ""))[:300]
                        if content.strip():
                            snippets.append(f"[session] {content}")
                    elif entry.get("type") == "PLANNER_RESPONSE":
                        content = str(entry.get("content", ""))[:300]
                        if content.strip():
                            snippets.append(f"[assistant] {content}")
                    if len(snippets) >= 20:
                        break
                except Exception:
                    pass
            if snippets:
                break  # Found a matching session, use it
        except Exception:
            pass

    return "\n".join(snippets)[:3000]


def _strip_path_prefix(text: str) -> str:
    """
    Claude Code VSCode extension prepends the active file path to every user message.
    E.g. "C:\\Users\\HP\\Downloads\\1\\main.py write fibonacci" → "write fibonacci"
    Strip that prefix so we get the real user intent.
    """
    import re as _re
    # Windows absolute path prefix: C:\...\anything.ext<space>rest
    text = _re.sub(r'^[A-Za-z]:\\[^\s]*\s*', '', text).strip()
    # Unix absolute path prefix: /home/.../file.ext<space>rest
    text = _re.sub(r'^/[^\s]*\s*', '', text).strip()
    # Relative file token at start: "main.py rest" or "index.js rest"
    text = _re.sub(r'^\S+\.\w{1,6}\s+', '', text).strip()
    return text


def _parse_claude_jsonl_line(line: str):
    """
    Parse one JSONL line from a Claude Code session file.
    Returns (role, content_text) or (None, None) if not a user/assistant message.
    Skips tool_result / tool_use blocks — only extracts plain text.
    Strips file-path prefixes injected by the VSCode extension.
    """
    import json as _json
    try:
        entry = _json.loads(line)
    except Exception:
        return None, None

    entry_type = entry.get("type", "")
    role = ""
    raw_content = None

    if entry_type in ("user", "assistant"):
        role = entry_type
        msg = entry.get("message", {}) or {}
        raw_content = msg.get("content", "")
    else:
        role = entry.get("role", "")
        raw_content = entry.get("content", "")

    if not role:
        return None, None

    # Extract only plain text blocks — skip tool_result, tool_use, image, document
    if isinstance(raw_content, list):
        text_parts = [
            block.get("text", "")
            for block in raw_content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        content = " ".join(text_parts).strip()
    else:
        content = str(raw_content).strip()

    # Strip file-path prefix that VSCode extension injects BEFORE checking content
    content = _strip_path_prefix(content)

    # Skip if still looks like a bare file path or is too short
    if content.startswith(("C:\\", "C:/", "/home/", "/Users/", "{")):
        return None, None
    if len(content) < 5:
        return None, None

    return role, content[:300]


def _find_claude_project_dir(project_path: Path):
    """
    Find the ~/.claude/projects/<dir> that best matches this project path.
    Claude sanitizes the full path into directory names like c--Users-HP-Downloads-1.
    We match by exact equality or suffix of the sanitized full path.
    Returns the most-specific (longest) matching dir, or None.
    """
    claude_projects_dir = Path.home() / ".claude" / "projects"
    if not claude_projects_dir.exists():
        return None

    # Sanitize project path the same way Claude does:
    # lowercase, remove separators, colons, dashes, underscores
    def _sanitize(s: str) -> str:
        return (s.lower()
                .replace("\\", "").replace("/", "")
                .replace(":", "").replace("-", "").replace("_", ""))

    proj_sanitized = _sanitize(str(project_path))

    try:
        candidates = [d for d in claude_projects_dir.iterdir() if d.is_dir()]
    except Exception:
        return None

    matches = []
    for d in candidates:
        d_sanitized = _sanitize(d.name)
        # Match only when the sanitized project path IS or ENDS WITH the sanitized dir name.
        # This prevents "c--Users-HP" from matching "C:\Users\HP\Downloads\1" because
        # "cusershpdownloads1" does NOT end with "cusershp" (it ends with "1").
        if proj_sanitized == d_sanitized or proj_sanitized.endswith(d_sanitized):
            matches.append((len(d_sanitized), d))

    if not matches:
        return None

    # Return the most specific (longest sanitized name = deepest path) match
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def scan_claude_sessions(project_path: Path) -> str:
    """
    Read Claude Code session transcripts for this project.
    Returns snippets with most-recent messages FIRST (for current_focus), followed by
    oldest messages (for goal/intent detection).

    Layout of returned string:
      Line 0..N  — most recent messages (most recent = line 0)
      Line N+1   — "---history---"
      Line N+2.. — oldest messages (oldest first)
    """
    proj_dir = _find_claude_project_dir(project_path)
    if proj_dir is None:
        return ""

    recent_snippets: list = []
    old_snippets: list = []

    try:
        jsonl_files = sorted(proj_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        for jf in jsonl_files[:2]:
            all_lines = jf.read_text(encoding="utf-8", errors="ignore").splitlines()

            # Recent: read last 250 lines reversed (most recent message first)
            for line in reversed(all_lines[-250:]):
                role, content = _parse_claude_jsonl_line(line)
                if role and content:
                    recent_snippets.append(f"[{role}] {content}")
                    if len(recent_snippets) >= 30:
                        break

            # Old: read first 150 lines forward (oldest message first)
            for line in all_lines[:150]:
                role, content = _parse_claude_jsonl_line(line)
                if role and content:
                    old_snippets.append(f"[{role}] {content}")
                    if len(old_snippets) >= 20:
                        break

            if recent_snippets:
                break
    except Exception:
        pass

    parts = recent_snippets[:]
    if old_snippets:
        parts.append("---history---")
        parts.extend(old_snippets)

    return "\n".join(parts)[:4000]


def scan_env_keys(project_path: Path) -> str:
    """Read .env / .env.example for service names (not values)."""
    for name in [".env.example", ".env.sample", ".env.template"]:
        p = project_path / name
        if p.exists():
            lines = []
            for line in _read_text(p, 1000).splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key = line.split("=")[0].strip()
                    lines.append(key)
            return "Env keys: " + ", ".join(lines)
    return ""


# ── Main entry point ─────────────────────────────────────────────────────────

def scan_source_code(project_path: Path) -> str:
    """
    Read the top of key source files so Gemini can understand what the code does.
    Reads: main entry points, top-level files, up to 60 lines each, max 6 files.
    """
    extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".cs"}
    # Prefer files that look like entry points
    priority_names = {"main.py", "app.py", "index.py", "server.py", "cli.py",
                      "main.js", "index.js", "app.js", "main.ts", "index.ts",
                      "main.go", "main.rs", "Program.cs"}
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__",
                 "dist", "build", ".autocheckpoint", ".cursor"}

    candidates = []
    try:
        for item in project_path.iterdir():
            if item.is_file() and item.suffix.lower() in extensions:
                priority = 0 if item.name in priority_names else 1
                candidates.append((priority, item))
        # Also walk one level deep
        for item in project_path.iterdir():
            if item.is_dir() and item.name not in skip_dirs:
                for sub in item.iterdir():
                    if sub.is_file() and sub.suffix.lower() in extensions:
                        priority = 0 if sub.name in priority_names else 2
                        candidates.append((priority, sub))
    except Exception:
        return ""

    candidates.sort(key=lambda x: x[0])
    snippets = []
    for _, fpath in candidates[:6]:
        try:
            lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()[:60]
            rel = str(fpath.relative_to(project_path))
            snippets.append(f"[{rel}]\n" + "\n".join(lines))
        except Exception:
            pass
    return "\n\n".join(snippets)[:4000]


def gather_all_signals(project_path: Path) -> dict:
    """
    Gather all context signals from the project and return as a dict.
    Each value is a string (may be empty if not found).
    """
    return {
        "readme":             scan_readme(project_path),
        "claude_md":          scan_claude_md(project_path),
        "cursor_rules":       scan_cursor_rules(project_path),
        "cursor_chat":        scan_cursor_chat(project_path),
        "claude_sessions":    scan_claude_sessions(project_path),
        "package_manifest":   scan_package_manifest(project_path),
        "git_log":            scan_git_log(project_path),
        "source_code":        scan_source_code(project_path),
        "todo_comments":      scan_todo_comments(project_path),
        "antigravity_sessions": scan_antigravity_sessions(project_path),
        "env_keys":           scan_env_keys(project_path),
    }


def build_prompt(project_path: Path, signals: dict) -> str:
    """Build the prompt to send to Gemini, requesting confidence and sources for every fact."""
    project_name = project_path.name
    parts = [
        f"You are analyzing a software project called '{project_name}'.",
        "Extract structured context from the signals below.",
        "",
        "Return ONLY a valid JSON object (no markdown fences, no extra text) with this exact shape:",
        "",
        '{',
        '  "intent": {',
        '    "value": "one sentence describing what this project builds",',
        '    "confidence": 0.85,',
        '    "sources": ["README.md", "package.json description"]',
        '  },',
        '  "current_focus": {',
        '    "value": "what is actively being worked on RIGHT NOW based on recent commits/TODOs",',
        '    "confidence": 0.70,',
        '    "sources": ["git: last 3 commits", "TODO in auth.py:42"]',
        '  },',
        '  "decisions": [',
        '    {"value": "short decision phrase", "confidence": 0.80, "sources": ["CLAUDE.md", "git commit abc123"]}',
        '  ],',
        '  "known_constraints": [',
        '    {"value": "short constraint phrase", "confidence": 0.75, "sources": ["README.md requirements"]}',
        '  ],',
        '  "tasks": [',
        '    {"value": "short task description", "confidence": 0.65, "sources": ["auth.py:42 TODO comment"]}',
        '  ]',
        '}',
        "",
        "Rules:",
        "- confidence is a float 0.0-1.0. Only include facts you can point to a specific source for.",
        "- If you cannot determine something from the signals, use empty string / empty list.",
        "- Do NOT invent facts not evidenced in the signals.",
        "- sources must name the actual file, commit, or signal type (e.g. 'git: commit a1b2c3 message').",
        "- Keep value fields concise: 1 sentence max for intent/focus, brief phrases for lists.",
        "",
        "=== CONTEXT SIGNALS ===",
    ]

    signal_labels = {
        "readme":               "README",
        "claude_md":            "CLAUDE.md / AI Instructions",
        "cursor_rules":         "Cursor AI Rules",
        "cursor_chat":          "Cursor Chat History",
        "claude_sessions":      "Claude Code Session History",
        "package_manifest":     "Package Manifests (package.json / pyproject.toml / etc.)",
        "git_log":              "Git Commit History",
        "source_code":          "Source Code (entry points / key files)",
        "todo_comments":        "TODO/FIXME Comments in Code",
        "antigravity_sessions": "Recent AI Session History (Antigravity IDE)",
        "env_keys":             "Environment Variable Keys",
    }

    for key, label in signal_labels.items():
        val = signals.get(key, "").strip()
        if val:
            parts.append(f"\n--- {label} ---\n{val}")

    return "\n".join(parts)
