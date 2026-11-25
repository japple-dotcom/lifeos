# PyInstaller recipe for the Life OS app download.
#
#   pip install -r requirements.txt -r requirements-app.txt
#   pyinstaller packaging/lifeos.spec --noconfirm
#
# Builds for the computer it runs on (PyInstaller can't build for another system; the GitHub workflow builds all):
#   Mac:      dist/Life OS.app          (menu bar app)
#   Windows:  dist/LifeOS.exe           (system tray app, one file)
#   Linux/Pi: dist/lifeos               (one file; runs with or without a screen)
#
# The app carries Python, every package, and a copy of the code (lifeos/, web/) as plain files under app/. The code is
# deliberately not compiled in: lifeos_boot.py picks either that copy or a newer one installed by an update.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is set by PyInstaller)
sys.path.insert(0, str(ROOT))
import lifeos_boot  # noqa: E402

MAC, WIN = sys.platform == "darwin", sys.platform == "win32"
STDLIB_SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data",
               "distutils", "msilib", "curses", "_tkinter", "antigravity", "this", "xxsubtype", "nis", "ossaudiodev",
               "spwd", "crypt", "readline", "dbm", "pty", "tty", "_curses", "_curses_panel", "sunau", "aifc", "chunk",
               "cgi", "cgitb", "imghdr", "mailcap", "nntplib", "pipes", "sndhdr", "telnetlib", "uu", "xdrlib"}

# Everything the code imports today, plus the rest of the standard library, so later updates can use it too.
hidden = collect_submodules("lifeos")
hidden += collect_submodules("uvicorn") + collect_submodules("apscheduler") + collect_submodules("googleapiclient")
hidden += collect_submodules("google.genai", filter=lambda n: ".tests" not in n) + collect_submodules("anthropic") + \
    collect_submodules("tzdata")
for name in sorted(getattr(sys, "stdlib_module_names", ())):
    if name not in STDLIB_SKIP and not name.startswith("_") and not (name in {"winreg", "msvcrt", "winsound", "_winapi"} and not WIN):
        hidden.append(name)
hidden += collect_submodules("email") + collect_submodules("xml") + collect_submodules("http") + \
    collect_submodules("concurrent") + collect_submodules("zoneinfo") + collect_submodules("sqlite3") + \
    collect_submodules("logging") + collect_submodules("urllib") + collect_submodules("json")
if MAC or WIN:
    hidden += ["pystray", "PIL.Image"] + collect_submodules("pystray")

datas = [(str(ROOT / "lifeos"), "app/lifeos"), (str(ROOT / "web"), "app/web"),
         (str(ROOT / "config" / "settings.yaml"), "app/config"), (str(ROOT / ".env.example"), "app"),
         (str(ROOT / "requirements.txt"), "app")]
datas += collect_data_files("googleapiclient") + collect_data_files("certifi") + collect_data_files("tzdata")
for pkg in ("apscheduler", "google-api-python-client", "fastapi", "uvicorn", "anthropic", "google-genai", "pydantic"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(  # noqa: F821
    [str(ROOT / "lifeos_boot.py")],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy.tests"],
    noarchive=False,
)
# The code ships as files (above), never compiled into the app, so updates can replace it.
a.pure = [m for m in a.pure if not (m[0] == "lifeos" or m[0].startswith("lifeos."))]
a.datas = [d for d in a.datas if "__pycache__" not in d[0] and not d[0].endswith(".pyc")]
pyz = PYZ(a.pure)  # noqa: F821
icon_png = ROOT / "web" / "icons" / "icon-512.png"
icon = str(ROOT / "packaging" / ("lifeos.icns" if MAC else "lifeos.ico")) if (MAC or WIN) else None
if icon and not Path(icon).exists():
    icon = None

if MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Life OS", console=False,  # noqa: F821
              target_arch=None, codesign_identity=None, entitlements_file=None, icon=icon)
    coll = COLLECT(exe, a.binaries, a.datas, name="Life OS")  # noqa: F821
    app = BUNDLE(coll, name="Life OS.app", icon=icon, bundle_identifier="uk.lifeos.app",  # noqa: F821
                 version=lifeos_boot.version_of(ROOT),
                 info_plist={"LSUIElement": True, "NSHighResolutionCapable": True,
                             "CFBundleShortVersionString": lifeos_boot.version_of(ROOT),
                             "LSMinimumSystemVersion": "11.0"})
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="LifeOS" if WIN else "lifeos",  # noqa: F821
              console=not WIN, upx=False, runtime_tmpdir=None, icon=icon)
