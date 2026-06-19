import re
import tarfile
import shutil
from pathlib import Path
from typing import Optional
from autocheckpoint.ignore import IgnoreMatcher

# Matches the autocheckpoint snapshot filename pattern: snapshot_YYYY-MM-DD_HH-MM-SS.tar.gz
_SNAPSHOT_FILE_RE = re.compile(r"snapshot_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tar\.gz$")

def restore_snapshot(snapshot_path: Path, target_dir: Path, ignore_matcher: Optional[IgnoreMatcher] = None) -> None:
    """
    Extracts a snapshot tarball to target_dir.
    Before extracting, we clean up the existing non-ignored files to avoid mixing.
    """
    target_dir = target_dir.resolve()

    # If target_dir doesn't exist, create it
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. If we have an ignore matcher, we clean up existing non-ignored files/folders
    # to ensure a clean restore of tracked files, leaving ignored files (like .git, node_modules) intact.
    if ignore_matcher:
        # Get list of files/folders in target_dir
        for item in target_dir.iterdir():
            # Skip .autocheckpoint/ directory to keep configuration
            if item.name == ".autocheckpoint":
                continue

            # If not ignored, we delete it
            if not ignore_matcher.is_ignored(item):
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()

    # 2. Extract tarball, skipping any bundled snapshot archives.
    # Old snapshots may have accidentally included the backup directory when it lived
    # inside the project tree. Filter those out so they don't appear in the restored folder.
    with tarfile.open(snapshot_path, "r:gz") as tar:
        members = [m for m in tar.getmembers() if not _SNAPSHOT_FILE_RE.search(m.name)]
        tar.extractall(path=target_dir, members=members)
