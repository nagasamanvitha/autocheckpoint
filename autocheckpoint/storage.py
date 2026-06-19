import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

class SnapshotStorage:
    def __init__(self, backup_dir_str: str, project_name: str):
        # Resolve backup dir (handle ~ or environment variables, and strip quotes)
        clean_path = backup_dir_str.strip('"\'')
        expanded = os.path.expanduser(os.path.expandvars(clean_path))
        self.backup_root = Path(expanded).resolve()
        self.project_name = project_name
        self.project_backup_dir = self.backup_root / project_name

    def ensure_storage_dirs(self) -> None:
        """Ensure storage directory exists."""
        self.project_backup_dir.mkdir(parents=True, exist_ok=True)

    def get_new_snapshot_path(self, timestamp: datetime) -> Path:
        """Generate a path for a new snapshot based on timestamp."""
        self.ensure_storage_dirs()
        time_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        return self.project_backup_dir / f"snapshot_{time_str}.tar.gz"

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """
        List all snapshots for the current project.
        Returns sorted list of dicts with:
        - path: Path object
        - timestamp: datetime object
        - size: int (bytes)
        """
        if not self.project_backup_dir.exists():
            return []
        
        snapshots = []
        for file in self.project_backup_dir.glob("snapshot_*.tar.gz"):
            # Extract timestamp
            # Format: snapshot_YYYY-MM-DD_HH-MM-SS.tar.gz
            name = file.name
            try:
                time_part = name.replace("snapshot_", "").replace(".tar.gz", "")
                dt = datetime.strptime(time_part, "%Y-%m-%d_%H-%M-%S")
                snapshots.append({
                    "path": file,
                    "timestamp": dt,
                    "size": file.stat().st_size
                })
            except (ValueError, OSError):
                continue
                
        # Sort by timestamp, oldest first or newest first? Let's return sorted by timestamp (oldest first, so we can reverse or list sequentially)
        snapshots.sort(key=lambda x: x["timestamp"])
        return snapshots
