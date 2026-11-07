from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project = Path(SPECPATH).parent
analysis = Analysis(
    [str(project / "desktop.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(project / "assets/avatars/deepy"), "assets/avatars/deepy"),
        (str(project / "web/assets/whale/art"), "web/assets/whale/art"),
    ],
    hiddenimports=collect_submodules("app") + collect_submodules("uvicorn"),
    hookspath=[str(project / "packaging/hooks")],
    excludes=["pytest", "PIL", "tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Dy-Ai-Live",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    contents_directory="_internal",
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Dy-Ai-Live",
)
