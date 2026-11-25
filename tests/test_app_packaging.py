"""The app download: where things are kept, setup, updates from a file or the feed, and going back by itself."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lifeos_boot  # noqa: E402
from lifeos import __version__, paths, setup_core, updater  # noqa: E402


def bump(v: str, n: int = 1) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + n}"


def package(version: str, runtime: int = 1, extra: dict | None = None, tamper: str = "", broken: bool = False) -> bytes:
    files = {"lifeos/__init__.py": f'__version__ = "{version}"\n',
             "lifeos/desktop.py": ("raise RuntimeError('broken build')\n" if broken else
                                   "def run(argv, boot=None):\n    return 0\n\ndef restart_args():\n    return [], ()\n"),
             "web/index.html": "<!doctype html><title>Life OS</title>", **(extra or {})}
    manifest = {"name": "lifeos-update", "version": version, "runtime": runtime, "notes": f"What's new in {version}",
                "files": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for k, v in files.items():
            z.writestr(k, v + tamper if k == "web/index.html" else v)
    return buf.getvalue()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_home_folder_is_per_user_and_settings_live_there(home, monkeypatch):
    assert paths.home() == home
    paths.ensure_home()
    assert (home / "config" / "settings.yaml").exists() and (home / "secrets").is_dir() and (home / "data").is_dir()
    from lifeos.config import load_settings
    s = load_settings()
    assert s.db_path == str(home / "data" / "lifeos.db")
    assert s.google_client_secret_file == str(home / "secrets" / "client_secret.json")
    monkeypatch.delenv("LIFEOS_HOME")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert paths.home() == paths.platform_home()  # the app download never keeps data inside the app


def test_first_setup_writes_keys_and_names_and_brings_an_old_install_across(home, tmp_path):
    assert setup_core.needs_setup()
    r = setup_core.first_setup(["Joe", "Sue"], ["Ava", "Leo"], "Leeds LS6", "claude", "sk-ant-test", "", "")
    key = r["household_key"]
    env = setup_core.read_env()
    assert env["LIFEOS_API_KEY"] == key and len(env["LIFEOS_SIGNING_SECRET"]) >= 24
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test" and env["LIFEOS_PUBLIC_URL"] == "http://localhost:8800"
    hh = setup_core.household()
    assert hh["names"] == {"me": "Joe", "wife": "Sue"} and hh["kids"] == ["Ava", "Leo"] and hh["area"] == "Leeds LS6"
    assert not setup_core.needs_setup()
    # Running it again keeps the same household key (phones stay signed in)
    assert setup_core.first_setup(["Joe", "Sue"], [], "", "gemini", "", "g-key", "")["household_key"] == key
    # Settings: only allowed keys change, and blank means "leave it"
    assert setup_core.update_env({"GEMINI_API_KEY": "new", "ANTHROPIC_API_KEY": "", "LIFEOS_API_KEY": "hack"}) == ["GEMINI_API_KEY"]
    assert setup_core.read_env()["LIFEOS_API_KEY"] == key
    with pytest.raises(ValueError):
        setup_core.update_env({"LIFEOS_PUBLIC_URL": "http://192.168.1.5:8800"})


def test_old_install_found_and_copied(tmp_path, monkeypatch):
    fake_home = tmp_path / "user"
    old = fake_home / "Documents" / "lifeos"
    (old / "lifeos").mkdir(parents=True)
    (old / "secrets").mkdir()
    (old / "data").mkdir()
    (old / "config").mkdir()
    (old / ".env").write_text("LIFEOS_API_KEY=maple-orbit-heron-tide-42\nLIFEOS_SIGNING_SECRET=" + "x" * 30 + "\n")
    (old / "secrets" / "google_token_me.json").write_text("{}")
    (old / "data" / "lifeos.db").write_bytes(b"sqlite")
    (old / "config" / "settings.yaml").write_text("household:\n  adults: [me, wife]\n  names: {me: Joe, wife: Sue}\n")
    monkeypatch.setenv("LIFEOS_HOME", str(tmp_path / "apphome"))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    found = setup_core.find_old_installs()
    assert found == [{"path": str(old), "names": ["Joe", "Sue"], "google": ["me"], "history": True}]
    setup_core.bring_across(str(old))
    assert (tmp_path / "apphome" / "secrets" / "google_token_me.json").exists()
    assert (tmp_path / "apphome" / "data" / "lifeos.db").read_bytes() == b"sqlite"
    assert not setup_core.needs_setup()
    with pytest.raises(ValueError):
        setup_core.bring_across("/etc")  # only folders it found itself


def test_setup_page_answers_only_this_computer(home):
    from lifeos.setup_web import create_setup_app

    class Ctl:
        restarts = []

        def request_restart(self, extra=None, drop=()):
            self.restarts.append((extra, drop))
    ctl = Ctl()
    app = create_setup_app(ctl, code="abc123")
    local = TestClient(app, client=("127.0.0.1", 5000))
    away = TestClient(app, client=("192.168.1.20", 5000))
    assert local.get("/health").json()["setup"] is True
    assert "Welcome to Life OS" in local.get("/setup").text
    assert away.get("/setup").status_code == 403
    assert away.get("/setup?code=abc123").status_code == 200   # the code shown on a screenless box
    assert away.get("/setup/api/state").status_code == 200      # remembered for this browser
    assert local.post("/setup/api/start", json={"mode": "normal"}).status_code == 400  # not set up yet
    r = local.post("/setup/api/save", json={"adults": ["Joe", "Sue"], "kids": ["Ava"], "provider": "claude"}).json()
    assert len(r["household_key"]) > 12
    assert local.post("/setup/api/start", json={"mode": "normal"}).json() == {"ok": True}
    assert ctl.restarts[-1] == ([], ("--demo",))
    local.post("/setup/api/start", json={"mode": "demo"})
    assert ctl.restarts[-1] == (["--demo"], ())
    assert local.post("/setup/api/save", json={"adults": ["Joe"], "public_url": "http://10.0.0.2"}).status_code == 400


def test_update_file_is_checked_before_anything_is_installed(home):
    newer = bump(__version__)
    with pytest.raises(updater.UpdateError, match="damaged"):
        updater.install_bytes(package(newer, tamper="<script>"))
    with pytest.raises(updater.UpdateError, match="already have"):
        updater.install_bytes(package(__version__))
    with pytest.raises(updater.UpdateError, match="isn't a Life OS update"):
        updater.install_bytes(b"not a zip")
    # A file trying to write outside its folder
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"name": "lifeos-update", "version": newer, "files": {"../evil.py": "0" * 64,
                                                                                                    "lifeos/__init__.py": "0"}}))
        z.writestr("../evil.py", "x")
    with pytest.raises(updater.UpdateError, match="Unexpected file"):
        updater.install_bytes(buf.getvalue())
    assert not (home / "versions").exists() or not any((home / "versions").iterdir())


def test_installed_update_runs_on_trial_then_is_kept(home):
    newer = bump(__version__)
    r = updater.install_bytes(package(newer))
    assert r["version"] == newer and r["restart"]
    active = json.loads((home / "versions" / "active.json").read_text())
    assert active["state"] == "trial" and active["version"] == newer
    code, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert code == home / "versions" / newer and v == newer and trial
    # It confirms itself once running happily (confirm_running is called by the new version)
    data = json.loads((home / "versions" / "active.json").read_text())
    (home / "versions" / "active.json").write_text(json.dumps({**data, "state": "ok", "tries": 0}))
    code, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert v == newer and not trial


def test_update_that_never_confirms_goes_back_by_itself(home):
    good, bad = bump(__version__), bump(__version__, 2)
    updater.install_bytes(package(good))
    active = json.loads((home / "versions" / "active.json").read_text())
    (home / "versions" / "active.json").write_text(json.dumps({**active, "state": "ok", "tries": 0}))
    # Pretend we're now running the good one, and install the bad one on top
    import lifeos
    old = lifeos.__version__
    try:
        updater.__version__ = good
        updater.install_bytes(package(bad))
    finally:
        updater.__version__ = old
    assert json.loads((home / "versions" / "active.json").read_text())["previous"] == good
    _, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert v == bad and trial
    # It started but never said it was fine (crashed, or was switched off): the next start goes back
    _, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert v == good and not trial
    assert bad in json.loads((home / "versions" / "active.json").read_text())["failed"]
    assert updater.status(type("S", (), {"store": _Store(), "settings": _Settings()})())["rolled_back"] == [bad]


def test_update_that_crashes_while_starting_goes_back_straight_away(home):
    bad = bump(__version__)
    updater.install_bytes(package(bad, broken=True))
    code, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert trial
    lifeos_boot.mark_failed(home, log=lambda *_: None)
    code, v, trial = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert code == lifeos_boot.bundled_code() and v == __version__ and not trial


def test_app_download_skips_updates_needing_newer_packages(home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(updater.UpdateError, match="new app download"):
        updater.install_bytes(package(bump(__version__), runtime=lifeos_boot.RUNTIME + 1))


def test_a_newer_app_download_wins_over_older_installed_updates(home, monkeypatch):
    newer = bump(__version__)
    updater.install_bytes(package(newer))
    monkeypatch.setattr(lifeos_boot, "version_of", lambda code: bump(__version__, 5))
    code, v, _ = lifeos_boot.choose_code(home, log=lambda *_: None)
    assert code == lifeos_boot.bundled_code() and json.loads((home / "versions" / "active.json").read_text())["version"] is None


class _Store:
    def __init__(self):
        self.d = {}

    def get_state(self, k, default=""):
        return self.d.get(k, default)

    def set_state(self, k, v):
        self.d[k] = v


class _Settings:
    behaviour: dict = {}
    public_url = "http://localhost:8800"


def test_update_feed_check_download_and_notify(home, monkeypatch):
    newer = bump(__version__)
    data = package(newer)
    feed = {"version": newer, "runtime": 1, "notes": "Faster mornings",
            "update": {"url": "https://example.test/u.zip", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
            "apps": {updater.platform_key(): "https://example.test/app"}}

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

        def iter_bytes(self):
            yield data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **k: Resp(feed))
    monkeypatch.setattr(httpx, "stream", lambda method, url, **k: Resp(None))
    pushes = []
    svc = type("Svc", (), {})()
    svc.store, svc.settings = _Store(), _Settings()
    svc.notifier = type("N", (), {"push": lambda self, msg, title="", actions=None: pushes.append((title, msg))})()
    monkeypatch.setenv("LIFEOS_UPDATE_FEED", "https://example.test/latest.json")
    st = updater.daily(svc)
    assert st["available"] and st["latest"] == newer and st["notes"] == "Faster mornings"
    assert pushes == [("Update ready", f"Life OS {newer} is ready. Faster mornings")]
    updater.daily(svc)
    assert len(pushes) == 1  # told once per version
    r = updater.download_and_install(svc)
    assert r["version"] == newer and (home / "versions" / newer / "lifeos" / "__init__.py").exists()
    # A download that doesn't match the feed is refused
    feed["update"]["sha256"] = "0" * 64
    with pytest.raises(updater.UpdateError):
        updater.download_and_install(svc)


def test_system_endpoints_install_an_update_file_and_restart(home, svc):
    from lifeos import desktop
    from lifeos.app import create_app

    class Ctl:
        def __init__(self):
            self.restarts = 0

        def request_restart(self, extra=None, drop=()):
            self.restarts += 1

        def request_quit(self):
            self.quit = True
    desktop.controller = ctl = Ctl()
    try:
        client = TestClient(create_app(svc, run_scheduler=False))
        client.post("/api/login", json={"key": "test-key", "person": "me"})
        info = client.get("/api/system").json()
        assert info["version"] == __version__ and info["home"] == str(home) and not info["updates"]["available"]
        r = client.post("/api/system/update/file", content=package(bump(__version__)),
                        headers={"Content-Type": "application/zip"}).json()
        assert r["version"] == bump(__version__) and r["restart"] and ctl.restarts == 1
        bad = client.post("/api/system/update/file", content=b"rubbish", headers={"Content-Type": "application/zip"})
        assert bad.status_code == 400 and "isn't a Life OS update" in bad.json()["detail"]
        assert client.post("/api/system/quit").json() == {"ok": True} and ctl.quit  # from this computer only
        phone = TestClient(create_app(svc, run_scheduler=False), client=("100.64.0.7", 50000))
        phone.post("/api/login", json={"key": "test-key", "person": "me"})
        assert phone.post("/api/system/quit").status_code == 403
        assert client.post("/api/system/keys", json={}).status_code == 400
    finally:
        desktop.controller = None
    anon = TestClient(create_app(svc, run_scheduler=False))
    assert anon.post("/api/system/update/file", content=b"x").status_code == 401  # signed-in adults only


def test_update_package_builder_matches_the_installer(home, tmp_path):
    sys.path.insert(0, str(ROOT / "packaging"))
    import make_update
    target = make_update.build(tmp_path / "dist")
    manifest, _ = updater.read_package(target.read_bytes())
    assert manifest["version"] == __version__ and manifest["runtime"] == lifeos_boot.RUNTIME
    assert "lifeos/app.py" in manifest["files"] and "web/app.js" in manifest["files"]
    assert not any(n.startswith((".env", "secrets", "data")) or n.endswith(".pyc") for n in manifest["files"] if n != ".env.example")
