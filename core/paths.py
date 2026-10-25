"""Resolve canonical project assets independently of the working directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_asset(path: str) -> str:
    value = Path(path).expanduser()
    candidates = (value, ROOT / value)
    return str(next((p.resolve() for p in candidates if p.is_file()), value))
