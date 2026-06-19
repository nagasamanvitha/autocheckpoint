import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

CONFIG_DIR = ".autocheckpoint"
CONFIG_FILE = "autocheckpoint.yaml"

def get_config_dir(project_path: Path) -> Path:
    return project_path / CONFIG_DIR

def get_config_file_path(project_path: Path) -> Path:
    return get_config_dir(project_path) / CONFIG_FILE

def is_initialized(project_path: Path) -> bool:
    return get_config_file_path(project_path).exists()

def load_config(project_path: Path) -> Dict[str, Any]:
    config_file = get_config_file_path(project_path)
    if not config_file.exists():
        return {}
    with open(config_file, "r", encoding="utf-8") as f:
        try:
            return yaml.safe_load(f) or {}
        except yaml.YAMLError:
            return {}

def save_config(project_path: Path, config_data: Dict[str, Any]) -> None:
    config_dir = get_config_dir(project_path)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / CONFIG_FILE
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, default_flow_style=False)
