"""The setup page, shown the first time Life OS runs (before there is a household key, so nobody can sign in yet).

Only answers this computer, or someone who has the short code Life OS printed when it started (for a Raspberry Pi
or server without a screen, set up from a laptop on the same network).
"""
from __future__ import annotations

import hmac
import ipaddress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, paths, setup_core


class SetupBody(BaseModel):
    adults: list[str]
    kids: list[str] = []
    area: str = ""
    provider: str = "claude"
    anthropic_key: str = ""
    gemini_key: str = ""
    public_url: str = ""
    ha_url: str = ""
    ha_token: str = ""


class BringBody(BaseModel):
    path: str


class StartBody(BaseModel):
    mode: str = "normal"   # normal | demo


def _local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


def create_setup_app(controller=None, code: str = "") -> FastAPI:
    app = FastAPI(title="Life OS setup", version=__version__)

    @app.middleware("http")
    async def this_computer_only(request: Request, call_next):
        path = request.url.path
        if path.startswith("/app/") or path == "/health":
            return await call_next(request)  # the styles and icons, and "is it running?"
        given = request.query_params.get("code") or request.cookies.get("lifeos_setup") or ""
        if not _local(request) and not (code and hmac.compare_digest(given, code)):
            return JSONResponse({"detail": "Finish setting up Life OS on the computer it's installed on, or open the "
                                           "address it showed when it started (it ends with ?code=...)."}, status_code=403)
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"detail": "Request from another website blocked."}, status_code=403)
        response = await call_next(request)
        if request.query_params.get("code") and code:
            response.set_cookie("lifeos_setup", code, httponly=True, samesite="strict", max_age=3600)
        return response

    @app.get("/health")
    def health():
        return {"setup": True, "version": __version__}

    @app.get("/")
    def root():
        return RedirectResponse("/setup")

    @app.get("/setup")
    def page():
        return FileResponse(paths.web_dir() / "setup.html", headers={"Cache-Control": "no-cache"})

    @app.get("/setup/api/state")
    def state():
        hh = setup_core.household()
        env = setup_core.read_env()
        names = [(hh.get("names") or {}).get(a, a) for a in hh.get("adults") or []]
        return {"version": __version__, "home": str(paths.home()), "old": setup_core.find_old_installs(),
                "adults": [n for n in names if n not in ("Me", "My wife")], "kids": hh.get("kids") or [],
                "area": hh.get("area") or "", "public_url": env.get("LIFEOS_PUBLIC_URL", "") if
                "your-tailnet" not in env.get("LIFEOS_PUBLIC_URL", "") else ""}

    @app.post("/setup/api/save")
    def save(body: SetupBody):
        adults = [a.strip() for a in body.adults if a.strip()]
        if not adults:
            raise HTTPException(400, "Type at least your own first name.")
        url = (body.public_url or "").strip()
        if url and not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise HTTPException(400, "The phone address must start with https:// (leave it blank for now if unsure).")
        return setup_core.first_setup(adults, [k.strip() for k in body.kids if k.strip()], body.area, body.provider,
                                      body.anthropic_key, body.gemini_key, url, body.ha_url, body.ha_token)

    @app.post("/setup/api/bring")
    def bring(body: BringBody):
        try:
            r = setup_core.bring_across(body.path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {**r, "ready": not setup_core.needs_setup()}

    @app.post("/setup/api/start")
    def start(body: StartBody):
        if body.mode != "demo" and setup_core.needs_setup():
            raise HTTPException(400, "Finish the setup first.")
        if controller is None:
            return {"ok": False, "detail": "Restart Life OS to carry on."}
        controller.request_restart(extra=["--demo"] if body.mode == "demo" else [], drop=() if body.mode == "demo" else ("--demo",))
        return {"ok": True}

    @app.get("/app/")
    def not_yet():
        return RedirectResponse("/setup")

    if paths.web_dir().exists():
        app.mount("/app", StaticFiles(directory=paths.web_dir(), html=False), name="app")
    return app
