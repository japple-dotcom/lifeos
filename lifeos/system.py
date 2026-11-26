"""Settings > This computer: version and updates, start with the computer, AI keys, restart and quit."""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import __version__, autostart, paths, setup_core, updater

log = logging.getLogger("lifeos.system")


class KeysBody(BaseModel):
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    public_url: str = ""
    ha_url: str = ""
    ha_token: str = ""
    dvla_api_key: str = ""
    octopus_tariff: str = ""
    gemini_model: str = ""
    mot_client_id: str = ""
    mot_client_secret: str = ""
    mot_api_key: str = ""
    mot_token_url: str = ""


class OnBody(BaseModel):
    on: bool


def _local(request: Request) -> bool:
    from .localonly import is_local
    return is_local(request)


def _controller():
    from . import desktop
    return desktop.controller


def add_routes(app: FastAPI, svc, current_person) -> None:
    def restart_soon() -> dict:
        ctl = _controller()
        if not ctl:
            return {"restart": False, "detail": "Restart Life OS to finish."}
        ctl.request_restart()
        return {"restart": True}

    @app.get("/api/system")
    def info(request: Request, person: str = Depends(current_person)):
        ctl = _controller()
        st = updater.status(svc)
        st["auto"] = auto_updates(svc)
        return {"version": __version__, "home": str(paths.home()), "app_download": paths.frozen(),
                "platform": updater.platform_key(), "updates": st, "local": _local(request),
                "autostart": {"supported": autostart.supported() and ctl is not None, "on": autostart.enabled()},
                "managed": ctl is not None, "demo": svc.settings.demo,
                "keys": {k: setup_core.is_set(setup_core.read_env().get(e)) for k, e in
                         (("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("ha", "HA_TOKEN"),
                          ("dvla", "DVLA_API_KEY"), ("mot", "MOT_API_KEY"))},
                "public_url": svc.settings.public_url, "gemini_model": svc.settings.gemini_model,
                "gemini_models": setup_core.GEMINI_MODELS}

    @app.post("/api/system/update/check")
    def check(person: str = Depends(current_person)):
        st = updater.check(svc, force=True)
        st["auto"] = auto_updates(svc)
        return st

    @app.post("/api/system/update/install")
    def install(person: str = Depends(current_person)):
        if svc.settings.demo:
            raise HTTPException(400, "The sample household can't be updated. Download the new app instead.")
        try:
            r = updater.download_and_install(svc)
        except updater.UpdateError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            log.exception("update failed")
            raise HTTPException(503, f"Couldn't download the update: {e}") from e
        svc.store.log_event("update", f"{__version__} -> {r['version']} by {person}")
        return {**r, **restart_soon()}

    @app.post("/api/system/update/file")
    async def install_file(request: Request, person: str = Depends(current_person)):
        if svc.settings.demo:
            raise HTTPException(400, "The sample household can't be updated. Download the new app instead.")
        data = await request.body()
        try:
            r = updater.install_bytes(data, source="file")
        except updater.UpdateError as e:
            raise HTTPException(400, str(e)) from e
        svc.store.log_event("update", f"{__version__} -> {r['version']} from a file, by {person}")
        return {**r, **restart_soon()}

    @app.post("/api/system/update/auto")
    def set_auto(body: OnBody, person: str = Depends(current_person)):
        svc.store.set_state("updates_auto", "1" if body.on else "")
        return {"auto": auto_updates(svc)}

    @app.post("/api/system/autostart")
    def set_autostart(body: OnBody, request: Request, person: str = Depends(current_person)):
        if not _local(request):
            raise HTTPException(403, "Change this on the computer Life OS runs on.")
        try:
            return {"on": autostart.set_enabled(body.on)}
        except Exception as e:
            raise HTTPException(503, f"Couldn't change that: {e}") from e

    @app.post("/api/system/keys")
    def keys(body: KeysBody, person: str = Depends(current_person)):
        try:
            changed = setup_core.update_env({"ANTHROPIC_API_KEY": body.anthropic_api_key, "GEMINI_API_KEY": body.gemini_api_key,
                                             "LIFEOS_PUBLIC_URL": body.public_url, "HA_URL": body.ha_url,
                                             "HA_TOKEN": body.ha_token, "DVLA_API_KEY": body.dvla_api_key,
                                             "OCTOPUS_TARIFF": body.octopus_tariff, "GEMINI_MODEL": body.gemini_model,
                                             "GEMINI_MODEL_DEEP": body.gemini_model, "MOT_CLIENT_ID": body.mot_client_id,
                                             "MOT_CLIENT_SECRET": body.mot_client_secret, "MOT_API_KEY": body.mot_api_key,
                                             "MOT_TOKEN_URL": body.mot_token_url})
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not changed:
            raise HTTPException(400, "Nothing to change: type a new value first.")
        svc.store.log_event("settings", f"{', '.join(changed)} changed by {person}")
        return {"changed": changed, **restart_soon()}

    @app.post("/api/system/restart")
    def restart(person: str = Depends(current_person)):
        return restart_soon()

    @app.post("/api/system/quit")
    def quit_app(request: Request, person: str = Depends(current_person)):
        ctl = _controller()
        if not _local(request) or not ctl:
            raise HTTPException(403, "Quit Life OS on the computer it runs on.")
        ctl.request_quit()
        return {"ok": True}

    @app.post("/api/system/leave-demo")
    def leave_demo(person: str = Depends(current_person)):
        ctl = _controller()
        if not svc.settings.demo or not ctl:
            raise HTTPException(400, "Not in the sample household.")
        ctl.request_restart(drop=("--demo",))
        return {"restart": True}


def auto_updates(svc) -> bool:
    return svc.store.get_state("updates_auto", "") == "1" or bool((svc.settings.behaviour.get("updates") or {}).get("auto"))
