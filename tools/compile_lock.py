"""Compile the Windows/Python 3.12 CPU environment; no application code runs."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
from piptools.scripts.compile import cli

if __name__ == "__main__":
    cli(
        [
            str(root / "requirements-cpu.in"),
            "--output-file",
            str(root / "requirements-cpu.lock"),
            "--no-header",
            "--no-annotate",
            "--strip-extras",
            "--allow-unsafe",
        ]
    )
