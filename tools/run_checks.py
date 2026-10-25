"""Run canonical tests in an isolated workspace scratch directory."""

from pathlib import Path
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = ROOT / ".runtime-tests" / uuid4().hex
temporary.mkdir(parents=True)
tempfile.tempdir = str(temporary)

if __name__ == "__main__":
    import pytest

    args = sys.argv[1:] or ["tests", "-q", "--tb=short"]
    raise SystemExit(
        pytest.main(
            [*args, "--basetemp", str(temporary / "pytest"), "-p", "no:cacheprovider"]
        )
    )
