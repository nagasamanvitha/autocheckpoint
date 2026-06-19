import os
import sys
from pathlib import Path

# Redirect stdout/stderr immediately for detached process (before importing rich or typer)
# This prevents NoneType/AttributeError crashes when libraries check stdout during import
if "--daemon-run" in sys.argv:
    try:
        project_path = Path.cwd()
        log_dir = project_path / ".autocheckpoint"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "watcher.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
        sys.__stdout__ = log_file
        sys.__stderr__ = log_file
    except Exception as e:
        with open("C:\\Users\\HP\\Downloads\\Autocheck\\redirect_error.txt", "w") as f:
            import traceback
            traceback.print_exc(file=f)

from datetime import datetime
import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.table import Table

from autocheckpoint import config
from autocheckpoint.ignore import IgnoreMatcher
from autocheckpoint.storage import SnapshotStorage
from autocheckpoint.watcher import ProjectWatcher
from autocheckpoint.restore import restore_snapshot
from autocheckpoint.utils import format_relative_time, is_pid_running
from autocheckpoint import context as ctx_module
from autocheckpoint.ai_context import auto_detect_context

app = typer.Typer(help="AutoCheckpoint - Never lose your code or your project context again.")
context_app = typer.Typer(help="Manage project context (goal, focus, decisions, constraints, tasks).")
app.add_typer(context_app, name="context")
console = Console()


# ── init ─────────────────────────────────────────────────────────────────────

@app.command()
def init(
    backup_path: str = typer.Option(
        None,
        "--backup-path",
        "-b",
        help="Path to the backup folder (e.g. Google Drive/AutoCheckpoint)"
    )
):
    """Initialize a new project checkpoint environment."""
    project_path = Path.cwd()

    if config.is_initialized(project_path):
        console.print("[yellow]Project is already initialized.[/yellow]")
        cfg = config.load_config(project_path)
        console.print(f"Current backup location: [cyan]{cfg.get('backup_location')}[/cyan]")
        overwrite = Prompt.ask("Do you want to re-initialize and overwrite?", choices=["y", "n"], default="n")
        if overwrite == "n":
            raise typer.Exit()

    is_cloud_sync = False
    while True:
        if not backup_path:
            console.print("\nWhere should backups live?")
            console.print("1. Local Only")
            console.print("2. Google Drive [green](recommended for cloud VMs)[/green]")
            console.print("3. OneDrive")
            console.print("4. Dropbox")
            
            sel = Prompt.ask("\nSelect option", choices=["1", "2", "3", "4"], default="2")
            if sel == "1":
                backup_path = Prompt.ask("Enter local backup path", default="~/autocheckpoint_backups")
            else:
                providers = {"2": "gdrive", "3": "onedrive", "4": "dropbox"}
                provider = providers[sel]
                
                console.print("\n[cyan]Why?[/cyan]")
                console.print("Cloud VMs are temporary.")
                console.print(f"Using {provider.capitalize()} ensures your snapshots survive VM deletion.\n")
                
                from autocheckpoint.rclone import configure_remote
                if configure_remote(provider):
                    console.print(f"\n[green]+ {provider.capitalize()} connected successfully![/green]")
                    backup_path = "~/autocheckpoint_backups"
                    is_cloud_sync = True
                else:
                    console.print(f"\n[red]Failed to configure {provider.capitalize()}. Falling back to local.[/red]")
                    backup_path = Prompt.ask("Enter local backup path", default="~/autocheckpoint_backups")

        backup_path = backup_path.strip('"\'')

        # Check if they typed a Windows-style path on a non-Windows OS
        if os.name != "nt" and not backup_path.startswith(("/", "~")):
            if ":" in backup_path or "\\" in backup_path:
                translated = translate_windows_path_to_linux(backup_path)
                console.print(f"\n[yellow]⚠ Warning: You entered a Windows-style path on a Linux/macOS machine.[/yellow]")
                console.print(f"Auto-translated to Linux path: [cyan]{translated}[/cyan]\n")
                confirm = Prompt.ask(f"Use the translated path '{translated}'?", choices=["y", "n"], default="y")
                if confirm == "y":
                    backup_path = translated
                else:
                    use_literal = Prompt.ask("Use the literal Windows path anyway?", choices=["y", "n"], default="n")
                    if use_literal == "y":
                        pass
                    else:
                        backup_path = None
                        continue

        expanded_path = os.path.expanduser(os.path.expandvars(backup_path))
        resolved_path = str(Path(expanded_path).resolve()).lower()
        home_dir = str(Path.home().resolve()).lower()
        
        is_in_home = resolved_path.startswith(home_dir)
        safe_keywords = ["drive", "onedrive", "dropbox", "cloud", "mount", "mnt", "media", "external", "share"]
        is_safe = any(k in resolved_path for k in safe_keywords)

        if is_in_home and not is_safe and not is_cloud_sync:
            console.print("")
            console.print(Panel(
                "[bold yellow]⚠ Warning[/bold yellow]\n\n"
                "This backup location is inside the current VM's local home directory.\n"
                "[bold red]If this VM is deleted, your backups will be deleted too![/bold red]\n\n"
                "Recommended alternatives:\n"
                " - Mounted shared folder (e.g. [cyan]/mnt/backups[/cyan] or [cyan]/media/shared[/cyan])\n"
                " - Google Drive / OneDrive sync directories\n"
                " - External disk / Network storage",
                border_style="yellow"
            ))
            confirm = Prompt.ask("Do you still want to use this local-only directory?", choices=["y", "n"], default="y")
            if confirm == "y":
                break
            else:
                backup_path = None
        else:
            break

    project_name = project_path.name

    config_data = {
        "backup_location": backup_path,
        "project_name": project_name,
        "interval_minutes": 0,  # 0 = snapshot on every change (debounced by 0.8s)
    }
    config.save_config(project_path, config_data)

    ignore_file = project_path / ".autocheckpointignore"
    if not ignore_file.exists():
        with open(ignore_file, "w", encoding="utf-8") as f:
            f.write("# Patterns to ignore from snapshots\n")
            f.write(".git/\n")
            f.write("node_modules/\n")
            f.write("venv/\n")
            f.write(".venv/\n")
            f.write("__pycache__/\n")
            f.write("dist/\n")
            f.write("build/\n")

    console.print("[green]+[/green] Project initialized")

    # Auto-start watcher
    start_auto = Prompt.ask("Start AutoCheckpoint automatically?", choices=["y", "n"], default="y")
    if start_auto == "y":
        pid_file = project_path / ".autocheckpoint" / "autocheckpoint.pid"
        is_running = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if is_pid_running(pid):
                    is_running = True
            except ValueError:
                pass

        if not is_running:
            import subprocess
            cmd = [sys.executable, "-u", "-m", "autocheckpoint.cli", "start", "--daemon-run"]
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0
            )
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(proc.pid))
            console.print("[green]+[/green] Background watcher started")
        else:
            console.print("[yellow]+[/yellow] Background watcher is already running")

    console.print(f"[green]+[/green] Backup location:\n  {backup_path}/{project_name}")

    # ── Auto-detect context (fully automatic, confidence-gated) ──────────────
    console.print("")
    console.print("[cyan]Scanning project for context (README, git log, AI sessions, code)...[/cyan]")

    try:
        detected = auto_detect_context(project_path)
        ctx_module.save_detected(project_path, detected)

        # Show what was detected and at what confidence
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column()
        table.add_column(style="dim", no_wrap=True)

        intent = detected.get("intent", {})
        if intent.get("value"):
            pct = f"{int(intent.get('confidence', 0) * 100)}%"
            table.add_row("Goal", intent["value"], pct)

        focus = detected.get("current_focus", {})
        if focus.get("value"):
            pct = f"{int(focus.get('confidence', 0) * 100)}%"
            table.add_row("Current focus", focus["value"], pct)

        for d in detected.get("decisions", []):
            if d.get("value"):
                pct = f"{int(d.get('confidence', 0) * 100)}%"
                table.add_row("Decision", d["value"], pct)

        for t in detected.get("tasks", []):
            if t.get("value"):
                pct = f"{int(t.get('confidence', 0) * 100)}%"
                table.add_row("Task", t["value"], pct)

        console.print(Panel(table, title="[bold]Context Auto-Detected[/bold]", border_style="cyan"))
        console.print(f"[green]+[/green] High-confidence facts (≥{int(ctx_module.CONFIDENCE_THRESHOLD * 100)}%) saved and synced to CLAUDE.md, .cursorrules, .windsurfrules")
        console.print("[dim]Run 'autocheckpoint explain' to see where each fact came from.[/dim]")

    except Exception:
        pass

    console.print("[dim]Tip: 'autocheckpoint handoff' for a full project state summary.[/dim]")


# ── start ────────────────────────────────────────────────────────────────────

@app.command()
def start(
    interval: float = typer.Option(None, "--interval", "-i", help="Backup interval in minutes (overrides config)"),
    background: bool = typer.Option(False, "--background", "-b", help="Run the watcher in the background"),
    daemon_run: bool = typer.Option(False, "--daemon-run", hidden=True, help="Internal flag for daemon mode"),
):
    """Start the file watcher to automatically snapshot changes."""
    project_path = Path.cwd()
    if not config.is_initialized(project_path):
        console.print("[red]Error: Project not initialized.[/red]")
        console.print("Run: [bold]autocheckpoint init[/bold] first.")
        raise typer.Exit(code=1)

    cfg = config.load_config(project_path)
    backup_location = cfg.get("backup_location")
    project_name = cfg.get("project_name", project_path.name)
    interval_minutes = interval if interval is not None else cfg.get("interval_minutes", 5.0)

    pid_file = project_path / ".autocheckpoint" / "autocheckpoint.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if pid != os.getpid() and is_pid_running(pid):
                console.print(f"[yellow]Watcher is already running (PID: {pid}).[/yellow]")
                raise typer.Exit()
        except ValueError:
            pass

    if background:
        import subprocess
        cmd = [sys.executable, "-u", "-m", "autocheckpoint.cli", "start", "--daemon-run"]
        if interval is not None:
            cmd.extend(["--interval", str(interval)])
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0
        )
        pid_file.write_text(str(proc.pid))
        console.print(f"[green]+[/green] Watcher started in background (PID: {proc.pid}).")
        console.print(f"  Logs: [cyan].autocheckpoint/watcher.log[/cyan]")
        console.print(f"  Stop: [bold]autocheckpoint stop[/bold]")
        raise typer.Exit()

    storage = SnapshotStorage(backup_location, project_name)
    ignore_matcher = IgnoreMatcher(project_path)
    watcher = ProjectWatcher(project_path, storage, ignore_matcher, interval_minutes)

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    try:
        watcher.start()
    finally:
        if pid_file.exists():
            pid_file.unlink()


# ── stop ─────────────────────────────────────────────────────────────────────

@app.command()
def stop():
    """Stop the background watcher."""
    project_path = Path.cwd()
    pid_file = project_path / ".autocheckpoint" / "autocheckpoint.pid"
    if not pid_file.exists():
        console.print("[yellow]No background watcher is currently running.[/yellow]")
        raise typer.Exit()

    pid_str = pid_file.read_text().strip()
    try:
        pid = int(pid_str)
        if is_pid_running(pid):
            if os.name == "nt":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 0)
                    ctypes.windll.kernel32.CloseHandle(handle)
            else:
                os.kill(pid, 15)
            console.print(f"[green]+[/green] Stopped background watcher (PID: {pid}).")
        else:
            console.print("[yellow]Stale watcher process detected. Cleaning up...[/yellow]")
    except (ValueError, OSError) as e:
        console.print(f"[red]Error stopping process: {e}[/red]")
    finally:
        if pid_file.exists():
            pid_file.unlink()


# ── status ───────────────────────────────────────────────────────────────────

@app.command()
def status():
    """Show current watching status and list of snapshots."""
    project_path = Path.cwd()
    if not config.is_initialized(project_path):
        console.print("[red]Error: Project is not initialized.[/red]")
        console.print("Run: [bold]autocheckpoint init[/bold] first.")
        raise typer.Exit(code=1)

    cfg = config.load_config(project_path)
    backup_location = cfg.get("backup_location")
    project_name = cfg.get("project_name", project_path.name)

    storage = SnapshotStorage(backup_location, project_name)
    snapshots = storage.list_snapshots()

    watcher_state = "[red]Not running[/red]"
    pid_file = project_path / ".autocheckpoint" / "autocheckpoint.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if is_pid_running(pid):
                watcher_state = f"[green]Running[/green] (PID: {pid})"
        except ValueError:
            pass

    console.print(f"\n[bold]Watching:[/bold] {project_name}")
    console.print(f"[bold]Watcher status:[/bold] {watcher_state}\n")

    if snapshots:
        last_snap = snapshots[-1]
        console.print(f"[bold]Last snapshot:[/bold]\n{format_relative_time(last_snap['timestamp'])}\n")
    else:
        console.print("[bold]Last snapshot:[/bold]\nNever\n")

    console.print(f"[bold]Snapshots:[/bold]\n{len(snapshots)}\n")
    console.print(f"[bold]Backup location:[/bold]\n{backup_location}/{project_name}\n")

    ctx = ctx_module.load_context(project_path)
    focus = ctx.get("current_focus", "")
    steps = ctx.get("recent_steps", [])
    open_tasks = [t for t in ctx.get("tasks", []) if t.get("status") != "done"]
    if focus or steps or open_tasks:
        console.print("[bold]Project context:[/bold]")
        if focus:
            console.print(f"  Focus:   {focus}")
        if steps:
            console.print(f"  History: {steps[0][:80]}")
        if open_tasks:
            console.print(f"  Tasks:   {len(open_tasks)} open")
        console.print("  [dim]Run 'autocheckpoint handoff' for full state[/dim]\n")


def translate_windows_path_to_linux(path_str: str) -> str:
    """
    If running on Linux/macOS and path looks like a Windows path (e.g., C:\\Users\\...),
    attempt to translate it to standard WSL mount point /mnt/c/Users/... or swap backslashes.
    """
    import re
    if os.name == "nt":
        return path_str
        
    # Check for drive letter like C:\\ or D:\\ or C:/
    match = re.match(r"^([a-zA-Z]):[/\\](.*)", path_str)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
        
    # If no drive letter but contains backslashes, convert them to forward slashes
    if "\\" in path_str:
        return path_str.replace("\\", "/")
        
    return path_str


def discover_backup_locations() -> list[Path]:
    """
    Search for possible backup locations.
    Checks common folders and searches the user's home directory.
    Returns a list of resolved Path objects that exist.
    """
    found = []
    
    # 1. Common paths
    candidates = [
        "~/autocheckpoint_backups",
        "~/Google Drive",
        "~/GoogleDrive",
        "~/OneDrive",
        "~/Dropbox",
        "/mnt/backups",
        "/mnt/shared",
        "~/backups",
    ]
    
    import sys
    # Let's also look for Google Drive / OneDrive under Library CloudStorage on macOS
    if sys.platform == "darwin":
        candidates.extend([
            "~/Library/CloudStorage/GoogleDrive",
            "~/Library/CloudStorage/OneDrive",
        ])
        
    for c in candidates:
        p = Path(os.path.expanduser(os.path.expandvars(c))).resolve()
        if p.exists() and p.is_dir():
            # Check if it has any subdirectory with a snapshot_*.tar.gz
            has_snapshots = False
            try:
                for sub in p.iterdir():
                    if sub.is_dir() and any(sub.glob("snapshot_*.tar.gz")):
                        has_snapshots = True
                        break
            except Exception:
                pass
            if has_snapshots:
                found.append(p)
                
    # 2. Search home directory (depth 2) for any folder named 'autocheckpoint_backups'
    try:
        home = Path.home()
        # Check ~/autocheckpoint_backups
        p1 = home / "autocheckpoint_backups"
        if p1.exists() and p1.is_dir() and p1 not in found:
            found.append(p1)
        # Check ~/*/autocheckpoint_backups
        for p in home.glob("*/autocheckpoint_backups"):
            if p.is_dir() and p not in found:
                found.append(p)
    except Exception:
        pass
        
    return found


# ── restore ──────────────────────────────────────────────────────────────────

@app.command()
def restore(
    latest: bool = typer.Option(False, "--latest", "-l", help="Instantly restore the most recent snapshot without prompting.")
):
    """Restore a snapshot from the backup folder."""
    project_path = Path.cwd()

    backup_location = None
    project_name = None
    is_cloud_restore = False
    cloud_provider = None

    if config.is_initialized(project_path):
        cfg = config.load_config(project_path)
        backup_location = cfg.get("backup_location")
        project_name = cfg.get("project_name", project_path.name)
        
        expanded = os.path.expanduser(os.path.expandvars(backup_location))
        backup_root = Path(expanded).resolve()
    else:
        console.print("[yellow]Project is not initialized in this directory (or VM was deleted).[/yellow]")
        
        # Discover local and cloud backup locations
        discovered_local = discover_backup_locations()
        
        from autocheckpoint.rclone import get_active_cloud_remotes, list_cloud_projects
        active_clouds = get_active_cloud_remotes()
        
        options = []
        # Gather local project backups
        for p in discovered_local:
            try:
                for sub in p.iterdir():
                    if sub.is_dir() and any(sub.glob("snapshot_*.tar.gz")):
                        options.append({
                            "type": "local",
                            "name": f"Local: {sub.name} (in {p})",
                            "project_name": sub.name,
                            "path": p
                        })
            except Exception:
                pass
                
        # Gather cloud project backups
        for cloud in active_clouds:
            projects = list_cloud_projects(cloud)
            for proj in projects:
                options.append({
                    "type": "cloud",
                    "name": f"Cloud ({cloud}): {proj}",
                    "project_name": proj,
                    "provider": cloud
                })
                
        if not options:
            # Fallback to prompting manually for a path/cloud if nothing discovered
            while True:
                console.print("\nWhere are the backups stored you want to restore from?")
                console.print("1. Local Only")
                console.print("2. Google Drive")
                console.print("3. OneDrive")
                console.print("4. Dropbox")
                
                sel = Prompt.ask("\nSelect option", choices=["1", "2", "3", "4"], default="2")
                if sel == "1":
                    backup_location = Prompt.ask("Enter local backup path", default="~/autocheckpoint_backups")
                    backup_location = backup_location.strip('"\'')
                    if os.name != "nt" and not backup_location.startswith(("/", "~")):
                        if ":" in backup_location or "\\" in backup_location:
                            translated = translate_windows_path_to_linux(backup_location)
                            console.print(f"\n[yellow]⚠ Warning: You entered a Windows-style path on a Linux/macOS machine.[/yellow]")
                            console.print(f"Auto-translated to Linux path: [cyan]{translated}[/cyan]\n")
                            confirm = Prompt.ask(f"Use the translated path '{translated}'?", choices=["y", "n"], default="y")
                            if confirm == "y":
                                backup_location = translated
                            else:
                                use_literal = Prompt.ask("Use the literal Windows path anyway?", choices=["y", "n"], default="n")
                                if use_literal == "y":
                                    pass
                                else:
                                    continue
                    
                    expanded = os.path.expanduser(os.path.expandvars(backup_location))
                    backup_root = Path(expanded).resolve()
                    
                    if not backup_root.exists():
                        console.print(f"[red]Error: Backup directory does not exist:[/red] {backup_root}")
                        raise typer.Exit(code=1)

                    project_dirs = [d for d in backup_root.iterdir() if d.is_dir()]
                    if not project_dirs:
                        console.print(f"[red]Error: No project backup directories found in[/red] {backup_root}")
                        raise typer.Exit(code=1)

                    if len(project_dirs) == 1:
                        project_name = project_dirs[0].name
                        console.print(f"Found project: [bold]{project_name}[/bold]")
                    else:
                        console.print("\nAvailable projects to restore:")
                        for idx, p_dir in enumerate(project_dirs, 1):
                            console.print(f"{idx}) {p_dir.name}")
                        console.print("")
                        sel_proj = Prompt.ask("Select a project to restore", choices=[str(i) for i in range(1, len(project_dirs) + 1)])
                        project_name = project_dirs[int(sel_proj) - 1].name
                    break
                else:
                    providers = {"2": "gdrive", "3": "onedrive", "4": "dropbox"}
                    cloud_provider = providers[sel]
                    
                    from autocheckpoint.rclone import configure_remote, list_cloud_projects
                    if configure_remote(cloud_provider):
                        projects = list_cloud_projects(cloud_provider)
                        if not projects:
                            console.print(f"[red]No projects found in {cloud_provider}.[/red]")
                            continue
                        
                        is_cloud_restore = True
                        if len(projects) == 1:
                            project_name = projects[0]
                            console.print(f"Found project: [bold]{project_name}[/bold]")
                        else:
                            console.print("\nAvailable projects to restore:")
                            for idx, p_dir in enumerate(projects, 1):
                                console.print(f"{idx}) {p_dir}")
                            sel_proj = Prompt.ask("Select a project to restore", choices=[str(i) for i in range(1, len(projects) + 1)])
                            project_name = projects[int(sel_proj) - 1]
                        break
                    else:
                        console.print(f"[red]Failed to connect to {cloud_provider}.[/red]")
                        continue
        else:
            console.print("\nDiscovered available backups to restore:")
            for idx, opt in enumerate(options, 1):
                console.print(f"{idx}) {opt['name']}")
            console.print(f"{len(options) + 1}) Specify custom path or provider...")
            
            sel = Prompt.ask("Select backup to restore", choices=[str(i) for i in range(1, len(options) + 2)])
            sel_idx = int(sel) - 1
            if sel_idx < len(options):
                selected_opt = options[sel_idx]
                project_name = selected_opt["project_name"]
                if selected_opt["type"] == "local":
                    backup_location = str(selected_opt["path"])
                    backup_root = Path(backup_location).resolve()
                else:
                    is_cloud_restore = True
                    cloud_provider = selected_opt["provider"]
            else:
                # Custom path/provider entry flow
                while True:
                    console.print("\nWhere are the backups stored you want to restore from?")
                    console.print("1. Local Only")
                    console.print("2. Google Drive")
                    console.print("3. OneDrive")
                    console.print("4. Dropbox")
                    
                    sel = Prompt.ask("\nSelect option", choices=["1", "2", "3", "4"], default="2")
                    if sel == "1":
                        backup_location = Prompt.ask("Enter local backup path", default="~/autocheckpoint_backups")
                        backup_location = backup_location.strip('"\'')
                        if os.name != "nt" and not backup_location.startswith(("/", "~")):
                            if ":" in backup_location or "\\" in backup_location:
                                translated = translate_windows_path_to_linux(backup_location)
                                console.print(f"\n[yellow]⚠ Warning: You entered a Windows-style path on a Linux/macOS machine.[/yellow]")
                                console.print(f"Auto-translated to Linux path: [cyan]{translated}[/cyan]\n")
                                confirm = Prompt.ask(f"Use the translated path '{translated}'?", choices=["y", "n"], default="y")
                                if confirm == "y":
                                    backup_location = translated
                                else:
                                    use_literal = Prompt.ask("Use the literal Windows path anyway?", choices=["y", "n"], default="n")
                                    if use_literal == "y":
                                        pass
                                    else:
                                        continue
                        
                        expanded = os.path.expanduser(os.path.expandvars(backup_location))
                        backup_root = Path(expanded).resolve()
                        
                        if not backup_root.exists():
                            console.print(f"[red]Error: Backup directory does not exist:[/red] {backup_root}")
                            raise typer.Exit(code=1)

                        project_dirs = [d for d in backup_root.iterdir() if d.is_dir()]
                        if not project_dirs:
                            console.print(f"[red]Error: No project backup directories found in[/red] {backup_root}")
                            raise typer.Exit(code=1)

                        if len(project_dirs) == 1:
                            project_name = project_dirs[0].name
                            console.print(f"Found project: [bold]{project_name}[/bold]")
                        else:
                            console.print("\nAvailable projects to restore:")
                            for idx, p_dir in enumerate(project_dirs, 1):
                                console.print(f"{idx}) {p_dir.name}")
                            console.print("")
                            sel_proj = Prompt.ask("Select a project to restore", choices=[str(i) for i in range(1, len(project_dirs) + 1)])
                            project_name = project_dirs[int(sel_proj) - 1].name
                        break
                    else:
                        providers = {"2": "gdrive", "3": "onedrive", "4": "dropbox"}
                        cloud_provider = providers[sel]
                        
                        from autocheckpoint.rclone import configure_remote, list_cloud_projects
                        if configure_remote(cloud_provider):
                            projects = list_cloud_projects(cloud_provider)
                            if not projects:
                                console.print(f"[red]No projects found in {cloud_provider}.[/red]")
                                continue
                            
                            is_cloud_restore = True
                            if len(projects) == 1:
                                project_name = projects[0]
                                console.print(f"Found project: [bold]{project_name}[/bold]")
                            else:
                                console.print("\nAvailable projects to restore:")
                                for idx, p_dir in enumerate(projects, 1):
                                    console.print(f"{idx}) {p_dir}")
                                sel_proj = Prompt.ask("Select a project to restore", choices=[str(i) for i in range(1, len(projects) + 1)])
                                project_name = projects[int(sel_proj) - 1]
                            break
                        else:
                            console.print(f"[red]Failed to connect to {cloud_provider}.[/red]")
                            continue

    if is_cloud_restore:
        from autocheckpoint.rclone import list_cloud_snapshots, download_cloud_snapshot
        snapshots = list_cloud_snapshots(project_name, cloud_provider)
        if not snapshots:
            console.print(f"[red]No snapshots found for project [bold]{project_name}[/bold] in cloud ({cloud_provider}).[/red]")
            raise typer.Exit(code=1)
            
        if len(snapshots) == 1:
            selected_snap = snapshots[0]
            time_str = selected_snap["timestamp"].strftime("%Y-%m-%d %I:%M %p")
            rel = format_relative_time(selected_snap["timestamp"])
            console.print(f"Restoring only available snapshot: [cyan]{time_str} ({rel})[/cyan]")
        else:
            console.print("\nAvailable recovery points in cloud:\n")
            for idx, snap in enumerate(snapshots, 1):
                time_str = snap["timestamp"].strftime("%Y-%m-%d %I:%M %p")
                rel = format_relative_time(snap["timestamp"])
                console.print(f"{idx}) {time_str} ({rel})")

            console.print("")
            sel = Prompt.ask("Select version to restore", choices=[str(i) for i in range(1, len(snapshots) + 1)])
            selected_snap = snapshots[int(sel) - 1]

        console.print(f"[yellow]Downloading snapshot from cloud...[/yellow]")
        temp_dir = Path(os.path.expanduser("~/autocheckpoint_backups")).resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_snap_path = temp_dir / selected_snap["name"]
        
        if not download_cloud_snapshot(selected_snap["path"], temp_snap_path):
            console.print("[red]Error: Failed to download snapshot from cloud.[/red]")
            raise typer.Exit(code=1)
            
        console.print(f"[yellow]Restoring snapshot...[/yellow]")
        ignore_matcher = IgnoreMatcher(project_path)
        restore_snapshot(temp_snap_path, project_path, ignore_matcher)
        
        try:
            temp_snap_path.unlink()
        except OSError:
            pass
            
        # Cloud restores default to storing future local snapshots in ~/autocheckpoint_backups
        backup_location = "~/autocheckpoint_backups"
    else:
        storage = SnapshotStorage(backup_location, project_name)
        snapshots = storage.list_snapshots()

        if not snapshots:
            console.print(f"[red]No snapshots found for project [bold]{project_name}[/bold].[/red]")
            raise typer.Exit(code=1)

        if latest or len(snapshots) == 1:
            selected_snap = snapshots[-1]
            time_str = selected_snap["timestamp"].strftime("%Y-%m-%d %I:%M %p")
            rel = format_relative_time(selected_snap["timestamp"])
            console.print(f"Restoring latest snapshot: [cyan]{time_str} ({rel})[/cyan]")
        else:
            console.print("\nAvailable recovery points:\n")
            for idx, snap in enumerate(snapshots, 1):
                time_str = snap["timestamp"].strftime("%Y-%m-%d %I:%M %p")
                rel = format_relative_time(snap["timestamp"])
                console.print(f"{idx}) {time_str} ({rel})")

            console.print("")
            sel = Prompt.ask("Select version to restore", choices=[str(i) for i in range(1, len(snapshots) + 1)])
            selected_snap = snapshots[int(sel) - 1]

        console.print(f"[yellow]Restoring snapshot...[/yellow]")

        ignore_matcher = IgnoreMatcher(project_path)
        restore_snapshot(selected_snap["path"], project_path, ignore_matcher)

    if not config.is_initialized(project_path):
        config_data = {
            "backup_location": backup_location,
            "project_name": project_name,
            "interval_minutes": 5.0
        }
        config.save_config(project_path, config_data)
        console.print(f"[green]+[/green] Re-initialized AutoCheckpoint config for project [bold]{project_name}[/bold]")

    console.print("[green]+ Restore complete![/green]")

    # Re-sync AI tool context files from restored context.yaml
    try:
        from autocheckpoint.sync import sync_tool_contexts
        restored_ctx = ctx_module.load_context(project_path)
        sync_tool_contexts(project_path, restored_ctx)
        console.print("[green]+[/green] AI tool context files synced (CLAUDE.md, .cursorrules, .windsurfrules)")
    except Exception:
        pass

    # Show handoff if it exists in restored snapshot
    handoff_path = project_path / ".autocheckpoint" / "handoff.md"
    if handoff_path.exists():
        console.print("\n[cyan]Project handoff found in snapshot. Run:[/cyan] [bold]autocheckpoint handoff[/bold]")


# ── handoff ──────────────────────────────────────────────────────────────────

@app.command()
def handoff(
    markdown: bool = typer.Option(
        False,
        "--markdown",
        "-m",
        help="Print raw Markdown output directly to stdout for copy-pasting into LLMs."
    )
):
    """
    Print a clean project state: goal, focus, decisions, constraints, tasks, recent changes.

    Saves to .autocheckpoint/handoff.md — bundled into the next snapshot automatically.
    Use this to hand context between AI sessions, VMs, or teammates.
    """
    project_path = Path.cwd()
    _require_init(project_path)

    ctx = ctx_module.load_context(project_path)
    project_name = ctx.get("project", {}).get("name", project_path.name)

    # Recent git log — only if this project has its own .git (avoid leaking parent repo history)
    import subprocess
    git_changes = []
    if (project_path / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=5
            )
            git_changes = result.stdout.strip().splitlines()
        except Exception:
            pass

    open_tasks = [t["text"] for t in ctx.get("tasks", []) if t.get("status") != "done"]
    done_tasks = [t["text"] for t in ctx.get("tasks", []) if t.get("status") == "done"]

    # ── Save handoff.md ───────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {project_name} -- Project Handoff",
        f"Generated: {ts}",
        "",
        "## Goal",
        ctx.get("intent", "") or "(not set)",
        "",
        "## Current Focus",
        ctx.get("current_focus", "") or "(not set)",
        "",
        "## Key Decisions",
    ]
    for d in ctx.get("decisions", []):
        lines.append(f"- {d['text']}")
    if not ctx.get("decisions"):
        lines.append("(none)")

    lines += ["", "## Known Constraints"]
    for c in ctx.get("known_constraints", []):
        lines.append(f"- {c['text']}")
    if not ctx.get("known_constraints"):
        lines.append("(none)")

    lines += ["", "## Open Tasks"]
    for t in open_tasks:
        lines.append(f"- [ ] {t}")
    if not open_tasks:
        lines.append("(none)")

    if done_tasks:
        lines += ["", "## Completed Tasks"]
        for t in done_tasks:
            lines.append(f"- [x] {t}")

    if git_changes:
        lines += ["", "## Recent Changes (git)"]
        for line in git_changes:
            lines.append(f"- {line}")

    summary = ctx.get("session_summary", "")
    if summary:
        lines += ["", "## Last Session Summary", summary]

    lines += [
        "",
        "---",
        "Restore: `autocheckpoint restore`",
        "Refresh context: `autocheckpoint context refresh`",
    ]

    handoff_content = "\n".join(lines)
    handoff_path = project_path / ".autocheckpoint" / "handoff.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(handoff_content, encoding="utf-8")

    if markdown:
        print(handoff_content)
        raise typer.Exit()

    # ── Rich display ──────────────────────────────────────────────────────────
    DIVIDER = "[dim]" + "-" * 56 + "[/dim]"

    console.print("")
    console.print(Panel(
        f"[bold white]{project_name}[/bold white]  [dim]-- Project Handoff[/dim]",
        border_style="bright_cyan",
        padding=(0, 2)
    ))

    def _section(title: str, body: str) -> None:
        console.print(f"\n[bold cyan]{title}[/bold cyan]")
        console.print(f"  {body}" if body else "  [dim]not set[/dim]")

    def _list(title: str, items: list, icon: str = "*") -> None:
        console.print(f"\n[bold cyan]{title}[/bold cyan]")
        if items:
            for item in items:
                console.print(f"  {icon} {item}")
        else:
            console.print("  [dim]none[/dim]")

    _section("Goal", ctx.get("intent", ""))
    _section("Current Focus", ctx.get("current_focus", ""))
    _list("Key Decisions", [d["text"] for d in ctx.get("decisions", [])])
    _list("Known Constraints", [c["text"] for c in ctx.get("known_constraints", [])], icon="!")
    _list("Open Tasks", open_tasks, icon="[ ]")
    if done_tasks:
        _list("Completed Tasks", done_tasks, icon="[x]")

    if git_changes:
        console.print(f"\n[bold cyan]Recent Changes (git)[/bold cyan]")
        for line in git_changes:
            console.print(f"  [dim]{line}[/dim]")

    if summary:
        _section("Last Session Summary", summary)

    console.print("\n" + DIVIDER)
    console.print("  [dim]Restore on a new machine:[/dim]  [bold]autocheckpoint restore[/bold]")
    console.print("  [dim]Refresh context with AI:[/dim]   [bold]autocheckpoint context refresh[/bold]")
    console.print(DIVIDER + "\n")
    console.print(f"[green]+[/green] Saved to [cyan].autocheckpoint/handoff.md[/cyan]  (bundled in next snapshot)")


# ── Context helper ────────────────────────────────────────────────────────────

@app.command()
def explain():
    """Show where every piece of auto-detected context came from."""
    project_path = Path.cwd()
    _require_init(project_path)

    ctx = ctx_module.load_context(project_path)
    detected = ctx.get("_detected", {})

    if not detected:
        console.print("[yellow]No auto-detected context found yet.[/yellow]")
        console.print("Run [bold]autocheckpoint init[/bold] or wait for the watcher to scan.")
        raise typer.Exit()

    threshold = ctx_module.CONFIDENCE_THRESHOLD

    def _confidence_color(c: float) -> str:
        if c >= 0.80:
            return "green"
        if c >= threshold:
            return "yellow"
        return "red"

    def _render_item(label: str, item: dict) -> None:
        val = item.get("value", "")
        conf = item.get("confidence", 0.0)
        sources = item.get("sources", [])
        color = _confidence_color(conf)
        pct = f"{int(conf * 100)}%"
        gate = "" if conf >= threshold else " [red](below threshold — not written to tool files)[/red]"
        console.print(f"\n[bold]{label}[/bold]  [{color}]{pct}[/{color}]{gate}")
        console.print(f"  [italic]\"{val}\"[/italic]")
        if sources:
            console.print("  [dim]Inferred from:[/dim]")
            for s in sources:
                console.print(f"    [dim]•[/dim] {s}")

    console.print(Panel(
        f"[bold]AutoCheckpoint — Context Provenance[/bold]\n"
        f"[dim]Threshold: {int(threshold * 100)}%  |  Facts below threshold are stored but not written to CLAUDE.md / .cursorrules / .windsurfrules[/dim]",
        border_style="cyan"
    ))

    intent = detected.get("intent", {})
    if intent.get("value"):
        _render_item("Goal", intent)

    focus = detected.get("current_focus", {})
    if focus.get("value"):
        _render_item("Current Focus", focus)

    for i, d in enumerate(detected.get("decisions", []), 1):
        if d.get("value"):
            _render_item(f"Decision {i}", d)

    for i, c in enumerate(detected.get("known_constraints", []), 1):
        if c.get("value"):
            _render_item(f"Constraint {i}", c)

    for i, t in enumerate(detected.get("tasks", []), 1):
        if t.get("value"):
            _render_item(f"Task {i}", t)

    console.print("")


def _require_init(project_path: Path) -> None:
    if not config.is_initialized(project_path):
        console.print("[red]Error: Project not initialized.[/red]")
        console.print("Run: [bold]autocheckpoint init[/bold] first.")
        raise typer.Exit(code=1)


def _print_context(project_path: Path) -> None:
    """Render the full context as a rich panel."""
    ctx = ctx_module.load_context(project_path)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()

    table.add_row("Project", ctx.get("project", {}).get("name", project_path.name))
    table.add_row("Current focus", ctx.get("current_focus") or "[dim]not set[/dim]")
    steps = ctx.get("recent_steps", [])
    if steps:
        table.add_row("Session history", "\n".join(f"  - {s[:100]}" for s in steps[:4]))

    decisions = ctx.get("decisions", [])
    table.add_row(
        "Key decisions",
        "\n".join(f"  * {d['text']}" for d in decisions) if decisions else "[dim]none recorded[/dim]"
    )

    constraints = ctx.get("known_constraints", [])
    table.add_row(
        "Known constraints",
        "\n".join(f"  ! {c['text']}" for c in constraints) if constraints else "[dim]none recorded[/dim]"
    )

    tasks = ctx.get("tasks", [])
    task_lines = []
    for i, t in enumerate(tasks, 1):
        icon = "[green][x][/green]" if t.get("status") == "done" else "[yellow][ ][/yellow]"
        task_lines.append(f"  {i}. {icon} {t['text']}")
    table.add_row("Tasks", "\n".join(task_lines) if task_lines else "[dim]none[/dim]")

    summary = ctx.get("session_summary", "")
    table.add_row(
        "Session summary",
        (summary[:120] + "..." if len(summary) > 120 else summary) if summary else "[dim]none recorded[/dim]"
    )

    console.print(Panel(table, title="[bold]Project Context[/bold]", border_style="cyan"))


# ── connect ──────────────────────────────────────────────────────────────────

@app.command()
def connect(
    provider: str = typer.Argument(
        ...,
        help="Cloud provider to connect (gdrive, onedrive, dropbox)"
    )
):
    """Connect a Google Drive, OneDrive, or Dropbox account via rclone."""
    from autocheckpoint import rclone
    if not rclone.is_rclone_installed():
        console.print(f"[red]Error: rclone is not installed.[/red]")
        console.print(rclone.get_install_instructions())
        raise typer.Exit(code=1)

    provider = provider.lower()
    if provider not in ("gdrive", "googledrive", "onedrive", "dropbox"):
        console.print(f"[red]Error: Unsupported cloud provider '{provider}'.[/red]")
        console.print("Supported providers: gdrive, onedrive, dropbox")
        raise typer.Exit(code=1)

    success = rclone.configure_remote(provider)
    if success:
        console.print(f"[green]+ Connected {provider} successfully![/green]")
    else:
        console.print(f"[red]Failed to configure {provider}.[/red]")
        raise typer.Exit(code=1)


# ── context subcommands ───────────────────────────────────────────────────────

@context_app.command("refresh")
def context_refresh():
    """Re-scan the project with AI and update context."""
    project_path = Path.cwd()
    _require_init(project_path)

    console.print("[cyan]Scanning project for context (README, git log, AI sessions, code)...[/cyan]")
    try:
        detected = auto_detect_context(project_path)
    except Exception as e:
        console.print(f"[red]Error during scan: {e}[/red]")
        raise typer.Exit(code=1)

    # Helper: extract plain string value from a detected field (may be dict or str)
    def _val(field) -> str:
        if isinstance(field, dict):
            return field.get("value", "") or ""
        return str(field) if field else ""

    def _list_vals(items) -> list:
        out = []
        for item in (items or []):
            v = _val(item)
            if v:
                out.append(v)
        return out

    intent_val   = _val(detected.get("intent"))
    focus_val    = _val(detected.get("current_focus"))
    dec_vals     = _list_vals(detected.get("decisions", []))
    con_vals     = _list_vals(detected.get("known_constraints", []))
    task_vals    = _list_vals(detected.get("tasks", []))
    steps_raw    = detected.get("recent_steps", {})
    steps_list   = steps_raw.get("value", []) if isinstance(steps_raw, dict) else []

    has_context = any([focus_val, steps_list, dec_vals, con_vals, task_vals])
    if not has_context:
        console.print("[yellow]No context signals detected.[/yellow]")
        console.print("Try adding a README.md, CLAUDE.md, or .cursorrules file.")
        raise typer.Exit()

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    if focus_val:
        table.add_row("Current focus", focus_val)
    if steps_list:
        table.add_row("Session history", "\n".join(f"  - {s[:100]}" for s in steps_list[:5]))
    if dec_vals:
        table.add_row("Decisions", "\n".join(f"  * {d}" for d in dec_vals))
    if con_vals:
        table.add_row("Constraints", "\n".join(f"  ! {c}" for c in con_vals))
    if task_vals:
        table.add_row("Open tasks", "\n".join(f"  - {t}" for t in task_vals))
    console.print(Panel(table, title="[bold]AI-Detected Context[/bold]", border_style="cyan"))

    save_ctx = Prompt.ask("Merge into your context? [Y/n/replace]", default="y").strip().lower()

    if save_ctx in ("y", "", "yes"):
        ctx_module.save_detected(project_path, detected)
        console.print("[green]+[/green] Context updated.")
        _print_context(project_path)
    elif save_ctx in ("r", "replace"):
        ctx = ctx_module.load_context(project_path)
        if intent_val:
            ctx["intent"] = intent_val
        ctx["current_focus"] = focus_val
        ctx["decisions"]         = [{"text": d, "recorded_at": ctx_module._now_iso()} for d in dec_vals]
        ctx["known_constraints"] = [{"text": c, "recorded_at": ctx_module._now_iso()} for c in con_vals]
        ctx["tasks"]             = [{"text": t, "status": "todo", "recorded_at": ctx_module._now_iso()} for t in task_vals]
        ctx["_detected"] = detected
        ctx_module.save_context(project_path, ctx)
        console.print("[green]+[/green] Context replaced.")
        _print_context(project_path)
    else:
        console.print("[dim]No changes made.[/dim]")


@context_app.command("show")
def context_show():
    """Show the full project context."""
    project_path = Path.cwd()
    _require_init(project_path)
    _print_context(project_path)


@context_app.command("set-intent")
def context_set_intent(intent: str = typer.Argument(None, help="Project goal (blank for prompt)")):
    """Set or update the project goal / intent."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not intent:
        intent = Prompt.ask("What are you building and why?")
    ctx_module.set_intent(project_path, intent)
    console.print(f"[green]+[/green] Goal saved: [italic]{intent}[/italic]")


@context_app.command("set-focus")
def context_set_focus(focus: str = typer.Argument(None, help="Current work focus (blank for prompt)")):
    """Update what you're currently working on."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not focus:
        focus = Prompt.ask("What are you currently working on?")
    ctx_module.set_current_focus(project_path, focus)
    console.print(f"[green]+[/green] Focus updated: [italic]{focus}[/italic]")


@context_app.command("add-decision")
def context_add_decision(decision: str = typer.Argument(None, help="Decision to record (blank for prompt)")):
    """Record an architectural or technology decision."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not decision:
        decision = Prompt.ask("What decision did you make?")
    ctx_module.add_decision(project_path, decision)
    console.print("[green]+[/green] Decision recorded.")


@context_app.command("add-constraint")
def context_add_constraint(constraint: str = typer.Argument(None, help="Constraint to record (blank for prompt)")):
    """Record a known constraint, limitation, or environment gotcha."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not constraint:
        constraint = Prompt.ask("What constraint or limitation should be noted?")
    ctx_module.add_constraint(project_path, constraint)
    console.print("[green]+[/green] Constraint recorded.")


@context_app.command("add-task")
def context_add_task(task: str = typer.Argument(None, help="Task to add (blank for prompt)")):
    """Add an open task to the project context."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not task:
        task = Prompt.ask("Describe the task")
    ctx_module.add_task(project_path, task)
    console.print("[green]+[/green] Task added.")


@context_app.command("done-task")
def context_done_task(index: int = typer.Argument(None, help="Task number to mark done (1-based)")):
    """Mark a task as done."""
    project_path = Path.cwd()
    _require_init(project_path)

    ctx = ctx_module.load_context(project_path)
    tasks = ctx.get("tasks", [])
    if not tasks:
        console.print("[yellow]No tasks recorded yet.[/yellow]")
        raise typer.Exit()

    if index is None:
        todo = [(i + 1, t) for i, t in enumerate(tasks) if t.get("status") != "done"]
        if not todo:
            console.print("[green]All tasks are already done![/green]")
            raise typer.Exit()
        console.print("\n[bold]Open tasks:[/bold]")
        for num, t in todo:
            console.print(f"  {num}. {t['text']}")
        index = int(Prompt.ask("Enter task number to mark done", choices=[str(n) for n, _ in todo]))

    result = ctx_module.complete_task(project_path, index)
    if result:
        console.print(f"[green]+[/green] Task {index} done: [italic]{result}[/italic]")
    else:
        console.print(f"[red]Task {index} not found.[/red]")


@context_app.command("add-session")
def context_add_session(summary: str = typer.Argument(None, help="Session summary (blank for prompt)")):
    """Record what happened in this work / AI session (overwrites previous summary)."""
    project_path = Path.cwd()
    _require_init(project_path)
    if not summary:
        summary = Prompt.ask("Summarize this session (what was built, decided, or changed?)")
    ctx_module.set_session_summary(project_path, summary)
    console.print("[green]+[/green] Session summary saved.")


@context_app.command("summarize")
def context_summarize():
    """Interactive wizard -- capture goal, focus, decisions, constraints, tasks in one shot."""
    project_path = Path.cwd()
    _require_init(project_path)

    ctx = ctx_module.load_context(project_path)
    console.print(Panel(
        "[bold cyan]Context Capture Wizard[/bold cyan]\nA few quick questions to capture your project state.",
        border_style="cyan"
    ))

    cur = ctx.get("intent", "")
    val = Prompt.ask("Project goal", default=cur or "")
    if val.strip():
        ctx_module.set_intent(project_path, val)

    cur = ctx.get("current_focus", "")
    val = Prompt.ask("Current focus", default=cur or "")
    if val.strip():
        ctx_module.set_current_focus(project_path, val)

    val = Prompt.ask("Key decision to record? (blank to skip)", default="")
    if val.strip():
        ctx_module.add_decision(project_path, val)

    val = Prompt.ask("Known constraint to record? (blank to skip)", default="")
    if val.strip():
        ctx_module.add_constraint(project_path, val)

    val = Prompt.ask("Open task to add? (blank to skip)", default="")
    if val.strip():
        ctx_module.add_task(project_path, val)

    val = Prompt.ask("Session summary (blank to skip)", default="")
    if val.strip():
        ctx_module.set_session_summary(project_path, val)

    console.print("\n[green]+ Context saved![/green]")
    _print_context(project_path)


@context_app.command("reset")
def context_reset():
    """Clear all auto-detected context and start fresh."""
    project_path = Path.cwd()
    _require_init(project_path)

    confirm = Prompt.ask(
        "[yellow]This will clear goal, focus, decisions, constraints, tasks, and detected data. Continue?[/yellow]",
        choices=["y", "n"], default="n"
    )
    if confirm != "y":
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit()

    from autocheckpoint import context as _ctx
    ctx = _ctx.load_context(project_path)
    ctx["intent"] = ""
    ctx["current_focus"] = ""
    ctx["decisions"] = []
    ctx["known_constraints"] = []
    ctx["tasks"] = []
    ctx["session_summary"] = ""
    ctx["_detected"] = {}
    _ctx.save_context(project_path, ctx)
    console.print("[green]+[/green] Context cleared. Run [bold]autocheckpoint context refresh[/bold] to re-detect.")



if __name__ == "__main__":
    app()
