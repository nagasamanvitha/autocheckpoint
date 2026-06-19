import os
import time
from pathlib import Path
from datetime import datetime
from rich.console import Console
from autocheckpoint.ignore import IgnoreMatcher
from autocheckpoint.storage import SnapshotStorage
from autocheckpoint.snapshot import create_snapshot

console = Console()

# How quickly we poll the directory (seconds)
_POLL_INTERVAL = 0.5
# After a file change, wait this long with NO further changes before snapshotting.
# Prevents creating a snapshot for every single keystroke during a multi-file save.
_QUIET_DEBOUNCE = 0.8
# Absolute minimum gap between two snapshots (prevents duplicates on rapid saves)
_MIN_SNAPSHOT_COOLDOWN = 5.0
# Re-scan context on every snapshot so goal/focus always reflects current code + sessions
_CONTEXT_REFRESH_EVERY_N_SNAPSHOTS = 1
# Hard ceiling: don't re-scan context more than once per 20 seconds even with rapid saves
_CONTEXT_REFRESH_MIN_INTERVAL = 20.0

class ProjectWatcher:
    def __init__(self, project_path: Path, storage: SnapshotStorage, ignore_matcher: IgnoreMatcher, interval_minutes: float = 5.0):
        self.project_path = project_path.resolve()
        self.storage = storage
        self.ignore_matcher = ignore_matcher
        self.interval_seconds = interval_minutes * 60
        self.running = False
        self.needs_snapshot = False
        self.last_change_time = 0.0
        self.last_snapshot_time = 0.0
        self.files_state = {}
        self._snapshots_since_context_refresh = 0
        self._last_context_refresh_time = 0.0
        # Track Claude session JSONL modification time for auto focus updates
        self._session_jsonl_path: Path | None = None
        self._session_jsonl_mtime: float = 0.0

    def _get_files_state(self) -> dict:
        """
        Scan directory and return a map of file path -> modification time.
        We skip ignored directories/files to optimize performance.
        """
        state = {}
        try:
            for root, dirs, files in os.walk(self.project_path):
                # Modify dirs in-place to avoid traversing ignored folders
                dirs[:] = [d for d in dirs if not self.ignore_matcher.is_ignored(Path(root) / d)]
                
                for f in files:
                    p = Path(root) / f
                    if not self.ignore_matcher.is_ignored(p):
                        try:
                            state[str(p.resolve())] = p.stat().st_mtime
                        except (OSError, FileNotFoundError):
                            pass
        except Exception:
            pass
        return state

    def _find_session_jsonl(self) -> "Path | None":
        """Return the most recently modified JSONL for this project's Claude session dir."""
        try:
            from autocheckpoint.scanner import _find_claude_project_dir
            proj_dir = _find_claude_project_dir(self.project_path)
            if not proj_dir:
                return None
            files = sorted(proj_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            return files[0] if files else None
        except Exception:
            return None

    def _update_focus_from_session(self) -> None:
        """
        Called whenever the Claude session JSONL changes (user sent a new message or AI replied).
        Updates current_focus AND recent_steps automatically — no manual refresh needed.
        """
        try:
            from autocheckpoint.scanner import scan_claude_sessions, _strip_path_prefix
            from autocheckpoint.ai_context import _extract_recent_steps
            from autocheckpoint import context as ctx_module

            session_text = scan_claude_sessions(self.project_path)
            if not session_text:
                return

            # Most recent user message = current focus
            recent_section = session_text.split("---history---", 1)[0] if "---history---" in session_text else session_text
            new_focus = ""
            for line in recent_section.splitlines():
                if line.startswith("[user]"):
                    content = _strip_path_prefix(line[6:].strip())
                    if len(content) > 10:
                        new_focus = content[:120]
                        break

            # Full recent steps history
            new_steps = _extract_recent_steps(session_text)

            ctx = ctx_module.load_context(self.project_path)
            changed = False

            if new_focus and ctx.get("current_focus") != new_focus:
                ctx["current_focus"] = new_focus
                changed = True

            if new_steps and ctx.get("recent_steps") != new_steps:
                ctx["recent_steps"] = new_steps
                changed = True

            if changed:
                ctx_module.save_context(self.project_path, ctx)
                label = new_focus[:60] if new_focus else "session updated"
                console.print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"[cyan]Context auto-updated: [bold]{label}[/bold][/cyan]"
                )
        except Exception:
            pass

    def _auto_detect_context(self) -> None:
        """
        Silently scan the project, score confidence, and merge only high-confidence
        facts into context.yaml. Also syncs CLAUDE.md / .cursorrules / .windsurfrules.
        """
        try:
            from autocheckpoint.ai_context import auto_detect_context
            from autocheckpoint import context as ctx_module

            detected = auto_detect_context(self.project_path)
            if not detected:
                return

            ctx_before = ctx_module.load_context(self.project_path)
            ctx_module.save_detected(self.project_path, detected)
            ctx_after = ctx_module.load_context(self.project_path)

            changed = (
                ctx_before.get("intent") != ctx_after.get("intent") or
                ctx_before.get("current_focus") != ctx_after.get("current_focus") or
                len(ctx_before.get("decisions", [])) != len(ctx_after.get("decisions", [])) or
                len(ctx_before.get("tasks", [])) != len(ctx_after.get("tasks", []))
            )

            if changed:
                console.print(f"[{datetime.now().strftime('%H:%M:%S')}] [cyan]Context auto-updated (CLAUDE.md, .cursorrules, .windsurfrules synced)[/cyan]")

            self._last_context_refresh_time = time.time()
            self._snapshots_since_context_refresh = 0

        except Exception as e:
            console.print(f"[dim]Context auto-detect skipped: {e}[/dim]")

    def start(self) -> None:
        self.running = True

        # Initialize the file state so we detect changes from this point forward
        self.files_state = self._get_files_state()

        console.print(f"[green]+[/green] Started AutoCheckpoint watcher for [bold]{self.storage.project_name}[/bold]")
        console.print(f"  Backup folder: [cyan]{self.storage.project_backup_dir}[/cyan]")
        console.print("  Watching for changes... (Press Ctrl+C to stop)")

        # Auto-detect context on startup if intent is not yet set
        try:
            from autocheckpoint import context as ctx_module
            ctx = ctx_module.load_context(self.project_path)
            if not ctx.get("intent"):
                console.print("[cyan]Auto-detecting project context...[/cyan]")
                self._auto_detect_context()
        except Exception:
            pass

        # Locate the Claude session JSONL for this project
        self._session_jsonl_path = self._find_session_jsonl()
        if self._session_jsonl_path:
            try:
                self._session_jsonl_mtime = self._session_jsonl_path.stat().st_mtime
            except Exception:
                self._session_jsonl_mtime = 0.0
            console.print(f"  Watching Claude session: [dim]{self._session_jsonl_path.name}[/dim]")

        # Take an initial snapshot immediately if there are no snapshots yet
        existing = self.storage.list_snapshots()
        if not existing:
            console.print("[yellow]No snapshots found. Creating initial snapshot...[/yellow]")
            self._take_snapshot()

        try:
            while self.running:
                time.sleep(_POLL_INTERVAL)

                # Check if the Claude session JSONL changed (new message sent)
                if self._session_jsonl_path:
                    try:
                        new_mtime = self._session_jsonl_path.stat().st_mtime
                        if new_mtime != self._session_jsonl_mtime:
                            self._session_jsonl_mtime = new_mtime
                            self._update_focus_from_session()
                    except Exception:
                        pass

                # Scan directory state
                current_state = self._get_files_state()
                if current_state != self.files_state:
                    self.files_state = current_state
                    self.needs_snapshot = True
                    self.last_change_time = time.time()

                now = time.time()
                # Snapshot as soon as the file stops changing AND the minimum cooldown has passed.
                # _MIN_SNAPSHOT_COOLDOWN (5s) prevents duplicate snapshots on rapid multi-file saves.
                # There is NO long interval gate — every change is saved almost immediately.
                quiet_enough   = (now - self.last_change_time  >= _QUIET_DEBOUNCE)
                cooldown_passed = (now - self.last_snapshot_time >= _MIN_SNAPSHOT_COOLDOWN)
                if self.needs_snapshot and quiet_enough and cooldown_passed:
                    self._take_snapshot()
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping watcher...[/yellow]")
        except BaseException as e:
            console.print(f"[red]Watcher exited due to error: {e.__class__.__name__} - {e}[/red]")
        finally:
            self.running = False

    def _take_snapshot(self) -> None:
        try:
            snapshot_file = create_snapshot(self.project_path, self.storage, self.ignore_matcher)
            self.needs_snapshot = False
            self.last_snapshot_time = time.time()
            self._snapshots_since_context_refresh += 1
            console.print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"[green]+ Saved:[/green] {snapshot_file.name}"
            )

            # Re-scan context after every snapshot (throttled to at most once per 20s)
            now = time.time()
            count_due = self._snapshots_since_context_refresh >= _CONTEXT_REFRESH_EVERY_N_SNAPSHOTS
            time_ok   = (now - self._last_context_refresh_time) >= _CONTEXT_REFRESH_MIN_INTERVAL
            if count_due and time_ok:
                self._auto_detect_context()
            
            # Automatically push to active cloud remotes (Google Drive, OneDrive, Dropbox)
            try:
                from autocheckpoint.rclone import get_active_cloud_remotes, upload_to_cloud
                active_remotes = get_active_cloud_remotes()
                for provider in active_remotes:
                    console.print(f"[cyan]Syncing snapshot to cloud ({provider})...[/cyan]")
                    if upload_to_cloud(self.storage.backup_root, self.storage.project_name, provider):
                        console.print(f"[green]+ Cloud sync ({provider}) successful![/green]")
                    else:
                        console.print(f"[yellow]⚠ Cloud sync ({provider}) failed.[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠ Cloud sync skipped: {e}[/yellow]")
                
        except Exception as e:
            console.print(f"[red]Failed to create snapshot: {e}[/red]")
