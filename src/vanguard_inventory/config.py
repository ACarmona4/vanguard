from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment(path: str | Path | None = None) -> Path:
    """Load the central environment file without replacing shell variables."""
    env_path = Path(path or os.getenv("VANGUARD_ENV_FILE", ".env")).expanduser()
    load_dotenv(dotenv_path=env_path, override=False)
    return env_path

