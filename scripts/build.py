"""Build the one-folder Windows distribution.

    uv run python scripts/build.py

Produces dist/Transcriber/Transcriber.exe (with its support files alongside) and zips the folder to
dist/Transcriber-<version>-windows.zip. The build is unsigned, so Windows SmartScreen warns on first
run; see the README.
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"
NAME = "Transcriber"


def version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def main() -> int:
    if sys.platform != "win32":
        print("The build is Windows-only (ADR-0001).", file=sys.stderr)
        return 1
    shutil.rmtree(DIST / NAME, ignore_errors=True)
    PyInstaller.__main__.run(
        [
            str(ROOT / "src" / "transcriber" / "__main__.py"),
            "--name", NAME,
            "--onedir",
            "--windowed",
            "--noconfirm",
            "--clean",
            "--paths", str(ROOT / "src"),
            "--distpath", str(DIST),
            "--workpath", str(WORK / "pyinstaller"),
            "--specpath", str(WORK),
            # Imported lazily by the app, so the analysis would otherwise miss them.
            "--hidden-import", "pyaudiowpatch",
            "--hidden-import", "keyring.backends.Windows",
            "--hidden-import", "transcriber.audio.wasapi",
            "--hidden-import", "transcriber.selfcheck",
            # Only the Qt modules the app uses; keeps the folder smaller.
            "--exclude-module", "PySide6.QtWebEngineCore",
            "--exclude-module", "PySide6.QtWebEngineWidgets",
            "--exclude-module", "PySide6.QtQml",
            "--exclude-module", "PySide6.QtQuick",
            "--exclude-module", "PySide6.Qt3DCore",
            "--exclude-module", "PySide6.QtMultimedia",
            "--exclude-module", "PySide6.QtCharts",
            "--exclude-module", "PySide6.QtDataVisualization",
            "--exclude-module", "PySide6.QtPdf",
        ]
    )
    folder = DIST / NAME
    if not (folder / f"{NAME}.exe").exists():
        print("Build failed: no executable produced.", file=sys.stderr)
        return 1
    archive = DIST / f"{NAME}-{version()}-windows"
    zip_path = Path(shutil.make_archive(str(archive), "zip", root_dir=DIST, base_dir=NAME))
    size_mb = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) / 1024**2
    print(f"\nBuilt {folder} ({size_mb:.0f} MB unpacked)")
    print(f"Zipped to {zip_path} ({zip_path.stat().st_size / 1024**2:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
