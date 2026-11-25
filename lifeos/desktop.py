"""Runs Life OS as an app: the server, the browser, an icon in the menu bar or system tray, restarts and quitting.

Started by lifeos_boot.py (which picks the version of the code to run). Three ways it can start:
- setup: nothing set up yet. A setup page opens in the browser (only on this computer, or with the code shown in
  the window/log on a machine without a screen).
- demo: sample data, nothing saved (`--demo`, or "Look around first" on the setup page).
- normal: the household's Life OS.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, paths

log = logging.getLogger("lifeos.desktop")
RESTART = 75
_restart = {"extra": [], "drop": ()}
controller: Controller | None = None  # set while running as the app; None under tests or `python -m lifeos.app`


def restart_args() -> tuple[list[str], tuple[str, ...]]:
    return _restart["extra"], _restart["drop"]


class Controller:
    """What the app's own pages can ask of the running app: restart (after an update or setup), or quit."""

    def __init__(self, mode: str, port: int, boot=None):
        self.mode, self.port, self.boot = mode, port, boot
        self.stop = threading.Event()
        self.code = 0
        self.server = None
        self.tray = None
        self.started = time.time()
        self.trial = bool(os.environ.get("LIFEOS_TRIAL"))
        self.key_token, self.key_token_until = "", 0.0

    def key_page(self) -> str:
        """A link to the 'household key' page that only works for 10 minutes and only from the menu bar icon."""
        import secrets
        self.key_token, self.key_token_until = secrets.token_urlsafe(24), time.time() + 600
        return self.url(f"/local/key?t={self.key_token}")

    def key_token_ok(self, token: str) -> bool:
        import hmac
        return bool(self.key_token) and time.time() < self.key_token_until and \
            hmac.compare_digest(token or "", self.key_token)

    def request_restart(self, extra: list[str] | None = None, drop: tuple[str, ...] = (), delay: float = 0.6) -> None:
        _restart.update(extra=list(extra or []), drop=tuple(drop))
        self._finish(RESTART, delay)

    def request_quit(self, delay: float = 0.6) -> None:
        self._finish(0, delay)

    def _finish(self, code: int, delay: float) -> None:
        def later():
            time.sleep(delay)  # let the page get its answer first
            self.code = code
            self.stop.set()
            if self.server:
                self.server.should_exit = True
            if self.tray:
                try:
                    self.tray.stop()
                except Exception:
                    pass
        threading.Thread(target=later, daemon=True).start()

    def url(self, path: str = "/app/") -> str:
        return f"http://localhost:{self.port}{path}"


# ---------- helpers ----------
def _setup_logging() -> Path:
    logfile = paths.home() / "data" / "lifeos.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # a windowed app has nowhere to print
        stream = open(logfile.with_name("lifeos-console.log"), "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    handler = logging.handlers.RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())
    return logfile


def _has_screen() -> bool:
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def already_running(port: int) -> dict | None:
    """Another Life OS on this computer already? Then just open it rather than starting a second one."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    except OSError:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:  # noqa: S310 (local)
            data = json.loads(r.read() or b"{}")
            return data if "version" in data or data.get("setup") else {"other": True}
    except Exception:
        return {"other": True}


def _lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outward-facing address
            return s.getsockname()[0]
    except OSError:
        return "this-computer"


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="lifeos", description="Life OS household assistant")
    p.add_argument("command", nargs="?", choices=["run", "setup", "install-service"], default="run")
    p.add_argument("--headless", action="store_true", help="no browser, no icon (servers, Raspberry Pi)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser (starting with the computer)")
    p.add_argument("--demo", action="store_true", help="sample data, nothing saved")
    p.add_argument("--port", type=int, default=int(os.environ.get("LIFEOS_PORT", "8800")))
    p.add_argument("--host", default=os.environ.get("LIFEOS_HOST", ""))
    p.add_argument("--home", default=None, help="where settings and history are kept")
    return p.parse_args(argv)


# ---------- the three ways to start ----------
def _build(mode: str, ctl: Controller, args):
    if mode == "demo":
        os.environ["LIFEOS_DEMO_PORT"] = str(args.port)
        from . import demo
        app, svc = demo.build()
        return app
    if mode == "setup":
        from . import setup_web
        return setup_web.create_setup_app(ctl, code=ctl.setup_code)
    from . import offsite
    restored = offsite.apply_pending()  # a backup chosen in Settings > Backups goes in before anything opens
    if restored:
        print(f"Restored a backup. The data from before is kept in {restored}")
    from .app import create_app
    return create_app()


def run(argv: list[str], boot=None) -> int:
    global controller
    args = _parse(argv)
    paths.ensure_home()
    os.chdir(paths.home())
    logfile = _setup_logging()

    if args.command == "setup":
        from .setup_core import cli
        cli()
        return 0
    if args.command == "install-service":
        from .autostart import install_linux_service
        return install_linux_service()

    from .setup_core import needs_setup
    mode = "demo" if args.demo else "setup" if needs_setup() else "normal"
    screen = _has_screen() and not args.headless
    other = already_running(args.port)
    if other:
        if other.get("other"):
            print(f"Something else is using port {args.port}. Close it, or start Life OS with --port 8801.")
            return 1
        print("Life OS is already running.")
        if screen and not args.no_browser:
            webbrowser.open(f"http://localhost:{args.port}/setup" if other.get("setup") else f"http://localhost:{args.port}/app/")
        return 0

    ctl = controller = Controller(mode, args.port, boot)
    ctl.setup_code = secrets.token_hex(3) if mode == "setup" else ""
    host = args.host or ("127.0.0.1" if mode == "demo" or (mode == "setup" and screen) else "0.0.0.0")  # noqa: S104
    app = _build(mode, ctl, args)

    import uvicorn
    config = uvicorn.Config(app, host=host, port=args.port, log_level="warning", proxy_headers=True,
                            forwarded_allow_ips="*")
    server = uvicorn.Server(config)
    ctl.server = server
    t = threading.Thread(target=server.run, name="lifeos-server", daemon=True)
    t.start()
    for _ in range(200):
        if server.started or not t.is_alive():
            break
        time.sleep(0.05)
    if not server.started:
        log.error("Life OS couldn't start its server (see %s)", logfile)
        raise RuntimeError("server did not start")

    page = "/setup" if mode == "setup" else "/app/"
    print(f"Life OS {__version__} running ({mode}): {ctl.url(page)}", flush=True)
    if mode == "setup" and not screen:
        print(f"Set it up from another computer on your network: http://{_lan_address()}:{args.port}/setup?code={ctl.setup_code}",
              flush=True)
    if mode == "demo":
        print("Sample data: household key 'demo'. Nothing is saved.", flush=True)
    if ctl.trial:
        threading.Thread(target=_confirm_when_settled, args=(ctl,), daemon=True).start()
    if screen and not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(ctl.url(page))).start()

    def on_signal(*_):
        ctl.request_quit(delay=0)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass

    def watch_server():  # if the server ever stops by itself, close the icon too
        t.join()
        ctl.stop.set()
        if ctl.tray:
            try:
                ctl.tray.stop()
            except Exception:
                pass
    threading.Thread(target=watch_server, daemon=True).start()

    if not (screen and _run_tray(ctl)):
        _wait(ctl, t)
    server.should_exit = True
    t.join(timeout=10)
    return ctl.code


def _wait(ctl: Controller, t: threading.Thread) -> None:
    while not ctl.stop.is_set() and t.is_alive():
        ctl.stop.wait(0.5)


def _confirm_when_settled(ctl: Controller) -> None:
    """A newly installed version is on trial until it has been running happily for a little while."""
    wait = float(os.environ.get("LIFEOS_TRIAL_SECONDS", "20"))
    time.sleep(wait)
    if ctl.stop.is_set():
        return
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ctl.port}/health", timeout=10) as r:  # noqa: S310 (local)
            ok = r.status == 200
    except Exception as e:
        log.warning("health check during trial failed: %s", e)
        ok = False
    if ok:
        from . import updater
        updater.confirm_running()
        log.info("Life OS %s confirmed after its update", __version__)
    elif ctl.boot is not None:
        ctl.boot.mark_failed(paths.home(), log=log.warning)  # go back to the version before
        ctl.request_restart(delay=0)


# ---------- menu bar / system tray icon ----------
def _run_tray(ctl: Controller) -> bool:
    """Shows the icon and blocks until Quit. False if this computer can't show one (then we just keep running)."""
    if os.environ.get("LIFEOS_NO_TRAY"):
        return False
    try:
        import pystray
        from PIL import Image
    except Exception:
        return False
    try:
        icon_file = paths.web_dir() / "icons" / "icon-192.png"
        image = Image.open(icon_file) if icon_file.exists() else Image.new("RGB", (64, 64), (10, 92, 83))
        from . import autostart

        def open_app(*_):
            webbrowser.open(ctl.url("/setup" if ctl.mode == "setup" else "/app/"))

        def toggle_login(*_):
            try:
                autostart.set_enabled(not autostart.enabled())
            except Exception as e:
                log.warning("start at login: %s", e)

        def show_key(*_):
            webbrowser.open(ctl.key_page())

        menu = pystray.Menu(
            pystray.MenuItem("Open Life OS", open_app, default=True),
            pystray.MenuItem("Show household key", show_key, visible=lambda _: ctl.mode == "normal"),
            pystray.MenuItem(f"Life OS {__version__}" + (" (sample data)" if ctl.mode == "demo" else ""), None, enabled=False),
            pystray.MenuItem("Start when I log in", toggle_login, checked=lambda _: autostart.enabled()),
            pystray.MenuItem("Quit Life OS", lambda *_: ctl.request_quit(delay=0)))
        ctl.tray = pystray.Icon("lifeos", image, "Life OS", menu)
        ctl.tray.run()
        return True
    except Exception as e:
        log.warning("no menu bar icon: %s", e)
        ctl.tray = None
        return False
