"""Routes for the v0.19 features (thin: the work is in each feature's own module).

Christmas, phone access (Tailscale Serve), meter readings, school kit, garden, the fridge photo, the weekly fridge
sheet, the home contents list and documents.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi import Body, Depends, HTTPException, Request
from fastapi.responses import Response

from . import (christmas, documents, fridge, garden, inventory, meters, phones, schoolkit, weeksheet)

log = logging.getLogger("lifeos.routes19")
BODY = Body(...)
OPTIONAL_BODY = Body(default=None)


def _call(fn):
    try:
        return fn()
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e)) from e


def add_routes(app, svc, agent, d: SimpleNamespace) -> None:
    person_dep = d.current_person

    # ---------- Christmas ----------
    @app.get("/api/christmas")
    def api_xmas(person: str = Depends(person_dep)):
        return christmas.plan(svc)

    @app.post("/api/christmas/{action}")
    def api_xmas_do(action: str, body: dict | None = OPTIONAL_BODY, person: str = Depends(person_dep)):
        b = body or {}
        acts = {
            "budget": lambda: christmas.set_budget(svc, b.get("total")),
            "person": lambda: christmas.add_person(svc, str(b.get("name", "")), b.get("budget") or 0),
            "person-update": lambda: christmas.update_person(svc, str(b.get("id", "")), b.get("budget"),
                                                             str(b.get("idea", "")), bool(b.get("remove"))),
            "bought": lambda: christmas.bought(svc, str(b.get("id", "")), str(b.get("what", "")), b.get("cost") or 0,
                                               str(b.get("where", ""))),
            "present": lambda: christmas.present(svc, str(b.get("id", "")), str(b.get("bid", "")), b.get("wrapped"),
                                                 bool(b.get("remove"))),
            "card": lambda: christmas.card(svc, str(b.get("name", "")), str(b.get("cid", "")), b.get("sent"),
                                           b.get("received"), bool(b.get("remove"))),
            "date": lambda: christmas.add_date(svc, str(b.get("what", "")), str(b.get("date", ""))),
            "todos": lambda: (d.need_google(), christmas.file_dates(svc, person))[1],
        }
        if action not in acts:
            raise HTTPException(404, "Not Found")
        return _call(acts[action])

    # ---------- phone access (Tailscale Serve on this computer) ----------
    @app.get("/api/system/phone-access")
    def api_phone_access(request: Request, person: str = Depends(person_dep)):
        from . import localonly
        url = svc.settings.public_url
        return {"cli": bool(phones.find_cli()), "url": url, "https": url.startswith("https://"),
                "here": localonly.is_local(request)}

    @app.post("/api/system/phone-access")
    def api_phone_access_setup(request: Request, person: str = Depends(person_dep)):
        from . import desktop, localonly
        if not localonly.is_local(request):
            raise HTTPException(403, "Do this on the computer Life OS runs on (open Life OS there and use Settings).")
        ctl = desktop.controller
        port = getattr(ctl, "port", 0) or 8800
        r = phones.setup(svc, port)
        if r.get("ok") and ctl:
            ctl.request_restart(delay=2)
            r["restart"] = True
        svc.store.log_event("phone_access", f"{'ok ' + r['url'] if r.get('ok') else 'not yet'} by {person}")
        return r

    # ---------- the Google sign-in file (Settings > Google accounts) ----------
    @app.post("/api/accounts/client")
    def api_google_client(body: dict = BODY, person: str = Depends(person_dep)):
        from . import oauth
        if svc.settings.demo:
            raise HTTPException(400, "The sample household doesn't use Google.")
        r = _call(lambda: oauth.save_client(svc.settings, str(body.get("file", ""))[:20000],
                                            str(body.get("client_id", "")), str(body.get("client_secret", ""))))
        svc.store.log_event("settings", f"Google sign-in file saved by {person}")
        return r

    # ---------- meter readings ----------
    @app.get("/api/meters")
    def api_meters(person: str = Depends(person_dep)):
        return meters.overview(svc)

    @app.post("/api/meters/reading")
    def api_meter_reading(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (meters.add_reading(svc, str(body.get("meter", "")), body.get("value"),
                                                 str(body.get("date", ""))), meters.overview(svc))[1])

    @app.post("/api/meters/settings")
    def api_meter_settings(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (meters.save_cfg(svc, body.get("meters"), body.get("day")), meters.overview(svc))[1])

    @app.post("/api/meters/read-now")
    def api_meter_read_now(person: str = Depends(person_dep)):
        return {"read": meters.read_sensors(svc), **meters.overview(svc)}

    @app.post("/api/meters/email")
    def api_meter_email(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: meters.email_draft(svc, str(body.get("meter", "")), person))

    # ---------- school kit ----------
    @app.get("/api/schoolkit")
    def api_schoolkit(person: str = Depends(person_dep)):
        return {"kids": schoolkit.cfg(svc), "tomorrow": schoolkit.tomorrow(svc)}

    @app.post("/api/schoolkit")
    def api_schoolkit_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: {"kids": schoolkit.save(svc, body.get("kids") or {}), "tomorrow": schoolkit.tomorrow(svc)})

    @app.post("/api/schoolkit/lost")
    def api_schoolkit_lost(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: schoolkit.lost(svc, str(body.get("kid", "")), str(body.get("item", "")), person))

    # ---------- garden ----------
    @app.get("/api/garden")
    def api_garden(person: str = Depends(person_dep)):
        return garden.overview(svc)

    @app.post("/api/garden")
    def api_garden_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (garden.save(svc, body.get("have"), body.get("weather"), body.get("postcode"),
                                          body.get("nudges")), garden.overview(svc))[1])

    @app.post("/api/garden/todo")
    def api_garden_todo(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: garden.job_todo(svc, str(body.get("text", "")), person))

    # ---------- the fridge ----------
    @app.post("/api/fridge/photo")
    async def api_fridge_photo(request: Request, note: str = "", freezer: bool = False,
                               person: str = Depends(person_dep)):
        data = await request.body()
        try:
            return fridge.from_photo(svc, agent, data, note, freezer)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/fridge/text")
    def api_fridge_text(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: fridge.from_text(svc, str(body.get("text", "")), bool(body.get("freezer"))))

    @app.post("/api/fridge/have")
    def api_fridge_have(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: fridge.have_it(svc, str(body.get("name", "")), body.get("uses") or [],
                                            body.get("missing") or [], str(body.get("day", ""))))

    @app.post("/api/fridge/shopping")
    def api_fridge_shopping(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return fridge.to_shopping(svc, body.get("items") or [])

    # ---------- the weekly fridge sheet ----------
    @app.get("/api/week-sheet")
    def api_week_sheet(start: str = "", person: str = Depends(person_dep)):
        from datetime import date, timedelta
        if not start:
            return weeksheet.build(svc)
        try:
            d = date.fromisoformat(start)
        except ValueError as e:
            raise HTTPException(400, "Give the date as YYYY-MM-DD.") from e
        return weeksheet.build(svc, d - timedelta(days=d.weekday()))

    # ---------- home contents ----------
    @app.get("/api/inventory")
    def api_inventory(person: str = Depends(person_dep)):
        return inventory.overview(svc)

    @app.post("/api/inventory")
    def api_inventory_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (inventory.save_item(svc, str(body.get("name", "")), str(body.get("room", "")),
                                                  body.get("value"), str(body.get("bought", "")),
                                                  str(body.get("serial", "")), str(body.get("notes", "")),
                                                  str(body.get("id", ""))), inventory.overview(svc))[1])

    @app.post("/api/inventory/remove")
    def api_inventory_remove(body: dict = BODY, person: str = Depends(person_dep)):
        inventory.remove_item(svc, str(body.get("id", "")))
        return inventory.overview(svc)

    @app.post("/api/inventory/cover")
    def api_inventory_cover(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: inventory.set_cover(svc, body.get("amount")))

    @app.get("/api/inventory.csv")
    def api_inventory_csv(person: str = Depends(person_dep)):
        return Response(inventory.as_csv(svc), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="home-contents.csv"',
                                 "Cache-Control": "no-store"})

    # ---------- documents ----------
    @app.get("/api/documents")
    def api_documents(q: str = "", person: str = Depends(person_dep)):
        out = documents.listing(svc)
        if q:
            out["answer"] = documents.where_is(svc, q) or "Nothing on the list matches that."
        return out

    @app.post("/api/documents")
    def api_documents_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (documents.save(svc, str(body.get("kind", "")), str(body.get("whose", "")),
                                             str(body.get("where", "")), str(body.get("expires", "")),
                                             str(body.get("notes", "")), str(body.get("id", ""))),
                              documents.listing(svc))[1])

    @app.post("/api/documents/remove")
    def api_documents_remove(body: dict = BODY, person: str = Depends(person_dep)):
        documents.remove(svc, str(body.get("id", "")))
        return documents.listing(svc)
