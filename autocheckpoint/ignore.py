import os
from pathlib import Path
import pathspec

DEFAULT_IGNORE_PATTERNS = [
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "build/",
    ".autocheckpoint/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
]

IGNORE_FILENAME = ".autocheckpointignore"

class IgnoreMatcher:
    def __init__(self, project_path: Path):
        self.project_path = project_path.resolve()
        self.patterns = list(DEFAULT_IGNORE_PATTERNS)
        
        # Load user patterns if .autocheckpointignore exists
        ignore_file = self.project_path / IGNORE_FILENAME
        if ignore_file.exists():
            with open(ignore_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.patterns.append(line)
        
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", self.patterns)

    def is_ignored(self, path: Path) -> bool:
        """
        Check if a given path (absolute or relative) is ignored.
        """
        try:
            rel_path = path.resolve().relative_to(self.project_path)
        except ValueError:
            # If path is not under project_path, ignore it or return True
            return True
            
        # Convert path to posix style string as expected by pathspec
        rel_str = rel_path.as_posix()
        
        # pathspec expects directories to end with a slash for directory matching
        if path.is_dir() and not rel_str.endswith("/"):
            rel_str += "/"
            
        return self.spec.match_file(rel_str)
