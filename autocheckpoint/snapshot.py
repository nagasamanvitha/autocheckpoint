import tarfile
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from autocheckpoint.ignore import IgnoreMatcher
from autocheckpoint.storage import SnapshotStorage


def create_snapshot(project_path: Path, storage: SnapshotStorage, ignore_matcher: IgnoreMatcher) -> Path:
    """
    Creates a snapshot tarball of the project (including context.yaml if present).
    Compresses to a temporary file first, then moves it to storage destination.
    """
    timestamp = datetime.now()
    dest_path = storage.get_new_snapshot_path(timestamp)

    # Ensure backup directory exists
    storage.ensure_storage_dirs()

    # Pre-resolve backup root so we can exclude it if it lives inside the project
    backup_root = storage.backup_root.resolve()

    # We write to a temporary file first to avoid corruption if interrupted
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tmp_name = tmp.name

    try:
        with tarfile.open(tmp_name, "w:gz") as tar:
            def tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
                actual_path = (project_path / tarinfo.name).resolve()

                # Always include context.yaml and handoff.md even if .autocheckpoint/ is ignored
                always_include = {
                    (project_path / ".autocheckpoint" / "context.yaml").resolve(),
                    (project_path / ".autocheckpoint" / "handoff.md").resolve(),
                }
                if actual_path in always_include:
                    return tarinfo

                # Auto-exclude the backup directory if it sits inside the project tree.
                # This prevents snapshot .tar.gz files from being bundled into other snapshots
                # and then re-appearing in the project on restore.
                try:
                    actual_path.relative_to(backup_root)
                    return None
                except ValueError:
                    pass

                if ignore_matcher.is_ignored(actual_path):
                    return None

                return tarinfo

            # Add the project root itself, matching files recursively
            tar.add(project_path, arcname=".", filter=tar_filter)

        # Move the temporary tarball to final destination
        shutil.move(tmp_name, dest_path)
        return dest_path

    except Exception as e:
        if Path(tmp_name).exists():
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass
        raise e
