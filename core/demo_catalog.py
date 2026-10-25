"""Only explicitly bundled sample files are exposed to the dashboard."""

from pathlib import Path
from core.paths import ROOT
from core.video_upload import VIDEO_EXTENSIONS


def demo_files():
    directory = (ROOT / "samples").resolve()
    if not directory.is_dir():
        return {}
    return {
        path.name: path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTENSIONS
        and path.resolve().parent == directory
        and not path.is_symlink()
    }


def demo_catalog():
    return [
        {
            "id": name,
            "name": Path(name).stem.replace("_", " "),
            "filename": name,
            "bytes": path.stat().st_size,
        }
        for name, path in demo_files().items()
    ]
