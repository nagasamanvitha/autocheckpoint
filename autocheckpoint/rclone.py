import subprocess
import shutil
import sys
import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

def is_rclone_installed() -> bool:
    """Check if rclone is installed in the system PATH."""
    return shutil.which("rclone") is not None

def get_install_instructions() -> str:
    """Get system-specific instructions for installing rclone."""
    if sys.platform == "win32":
        return (
            "rclone is not installed. Please install it on Windows:\n"
            "  * Using winget: winget install Rclone.Rclone\n"
            "  * Or download from: https://rclone.org/downloads/\n"
        )
    elif sys.platform == "darwin":
        return (
            "rclone is not installed. Please install it on macOS:\n"
            "  * Using Homebrew: brew install rclone\n"
        )
    else:
        return (
            "rclone is not installed. Please install it on Linux:\n"
            "  * Using apt: sudo apt-get update && sudo apt-get install -y rclone\n"
            "  * Or run: curl https://rclone.org/install.sh | sudo bash\n"
        )

def install_rclone() -> bool:
    """Attempt to auto-install rclone based on the OS."""
    if is_rclone_installed():
        return True
        
    if sys.platform == "win32":
        print("Attempting to install rclone using winget...", file=sys.stderr)
        return subprocess.run("winget install Rclone.Rclone", shell=True).returncode == 0
    elif sys.platform == "darwin":
        print("Attempting to install rclone using Homebrew...", file=sys.stderr)
        return subprocess.run("brew install rclone", shell=True).returncode == 0
    else:
        print("Attempting to install rclone using curl script...", file=sys.stderr)
        return subprocess.run("curl https://rclone.org/install.sh | sudo bash", shell=True).returncode == 0

def get_rclone_remote_name(provider: str) -> str:
    """Standardized remote name for autocheckpoint providers."""
    return f"autocheckpoint_{provider.lower()}"

def configure_remote(provider: str) -> bool:
    """Run the interactive/headless configuration flow for rclone."""
    if not is_rclone_installed():
        if not install_rclone():
            print(get_install_instructions(), file=sys.stderr)
            return False

    provider = provider.lower()
    remote_name = get_rclone_remote_name(provider)

    # Map our simplified names to rclone providers
    provider_map = {
        "gdrive": "drive",
        "googledrive": "drive",
        "onedrive": "onedrive",
        "dropbox": "dropbox"
    }

    rclone_type = provider_map.get(provider)
    if not rclone_type:
        print(f"Error: Unsupported provider '{provider}'", file=sys.stderr)
        return False

    print(f"\nInitializing headless authentication for {provider}...")
    print("If you are on a remote server/VM, please copy the URL displayed below,")
    print("open it in your local web browser, authorize the app, and paste the code/token back here.\n")

    # Command to create a base remote without a token first
    cmd1 = ["rclone", "config", "create", remote_name, rclone_type, "config_is_local=false"]
    try:
        subprocess.run(cmd1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Now reconnect to trigger the interactive token flow
        cmd2 = ["rclone", "config", "reconnect", f"{remote_name}:"]
        res = subprocess.run(cmd2)
        return res.returncode == 0
    except Exception as e:
        print(f"Error during rclone config: {e}", file=sys.stderr)
        return False

def get_active_cloud_remotes() -> List[str]:
    """List all autocheckpoint_ rclone remotes currently configured."""
    if not is_rclone_installed():
        return []
    
    try:
        # Run rclone listremotes
        res = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return []
        
        remotes = []
        for line in res.stdout.splitlines():
            name = line.strip().rstrip(":")
            if name.startswith("autocheckpoint_"):
                remotes.append(name.replace("autocheckpoint_", ""))
        return remotes
    except Exception:
        return []

def upload_to_cloud(local_dir: Path, project_name: str, provider: str) -> bool:
    """Sync/copy local snapshot files of a project to the cloud remote."""
    if not is_rclone_installed():
        return False

    remote_name = get_rclone_remote_name(provider)
    # Source local dir is specific to the project: local_dir / project_name
    src = str(local_dir / project_name)
    dst = f"{remote_name}:{project_name}"

    try:
        # Using copy instead of sync so we don't accidentally delete remote files if local ones are missing
        cmd = ["rclone", "copy", src, dst, "--transfers", "1"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def list_cloud_snapshots(project_name: str, provider: str) -> List[Dict[str, Any]]:
    """List all snapshots available in the cloud for a project."""
    if not is_rclone_installed():
        return []

    remote_name = get_rclone_remote_name(provider)
    path = f"{remote_name}:{project_name}"

    try:
        # Get directory contents in JSON format
        cmd = ["rclone", "lsjson", path, "--include", "snapshot_*.tar.gz"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return []
        
        items = json.loads(res.stdout)
        snapshots = []
        from datetime import datetime
        for item in items:
            name = item["Name"]
            try:
                time_part = name.replace("snapshot_", "").replace(".tar.gz", "")
                dt = datetime.strptime(time_part, "%Y-%m-%d_%H-%M-%S")
                snapshots.append({
                    "name": name,
                    "timestamp": dt,
                    "size": item["Size"],
                    "path": f"{path}/{name}"
                })
            except (ValueError, KeyError):
                continue
        
        snapshots.sort(key=lambda x: x["timestamp"])
        return snapshots
    except Exception:
        return []

def download_cloud_snapshot(cloud_path: str, local_dest: Path) -> bool:
    """Download a specific snapshot file from cloud to local path."""
    if not is_rclone_installed():
        return False
    
    try:
        cmd = ["rclone", "copyto", cloud_path, str(local_dest)]
        subprocess.run(cmd, check=True)
        return True
    except Exception:
        return False

def list_cloud_projects(provider: str) -> List[str]:
    """List all project directories available on the cloud remote."""
    if not is_rclone_installed():
        return []
    remote_name = get_rclone_remote_name(provider)
    try:
        # Run rclone lsf with dirs-only to list top level folders (each folder is a project)
        res = subprocess.run(["rclone", "lsf", f"{remote_name}:", "--dirs-only"], capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return []
        return [line.strip().rstrip("/") for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        return []

