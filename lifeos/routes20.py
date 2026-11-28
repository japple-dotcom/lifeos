"""Routes for the v0.20 features (thin: the work is in each feature's own module).

The Google set-up check, brain dump, snap a letter, email to to-do, the "Tomorrow" message, the freezer, storage
boxes with QR labels, guests, something's broken, and the kids' routines (the tablet may tick those).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi import Body, Depends, HTTPException, Request

from . import (boxes, braindump, broken, freezer, googlesetup, guests, letters, mailtodo, routines, tomorrow)

log = logging.getLogger("lifeos.routes20")
BODY = Body(...)
OPTIONAL_BODY = Body(default=None)


def _call(fn):
    try:
        return fn()
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e)) from e


def add_routes(app, svc, agent, d: SimpleNamespace) -> None:
    person_dep, kids_dep = d.current_person, d.current_kids

    # ---------- Google set-up check ----------
    @app.get("/api/google/setup-check")
    def api_google_setup(person: str = Depends(person_dep)):
        if svc.settings.demo:
            return {"steps": [], "summary": "The sample household doesn't use Google.", "ok": True, "project": "",
                    "redirect": ""}
        return googlesetup.check(svc)

    # ---------- brain dump and letters ----------
    @app.post("/api/braindump/split")
    def api_braindump_split(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: braindump.split(svc, agent, str(body.get("text", ""))))

    @app.post("/api/braindump/save")
    def api_braindump_save(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        origin = "letter" if body.get("origin") == "letter" else "brain dump"
        return _call(lambda: braindump.save_items(svc, person, body.get("items") or [], origin))

    @app.post("/api/letters/read")
    async def api_letter_read(request: Request, person: str = Depends(person_dep)):
        data = await request.body()
        try:
            return letters.read_photo(svc, agent, data)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # ---------- email to to-do ----------
    @app.post("/api/mailtodo/run")
    def api_mailtodo_run(person: str = Depends(person_dep)):
        d.need_google()
        return {"added": mailtodo.run(svc, agent)}

    # ---------- the "Tomorrow" message ----------
    @app.get("/api/tomorrow")
    def api_tomorrow(person: str = Depends(person_dep)):
        return {"settings": tomorrow.cfg(svc), "preview": tomorrow.build(svc)}

    @app.post("/api/tomorrow")
    def api_tomorrow_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: {"settings": tomorrow.save(svc, body.get("on"), body.get("at"), body.get("speak")),
                              "preview": tomorrow.build(svc)})

    # ---------- freezer ----------
    @app.get("/api/freezer")
    def api_freezer(person: str = Depends(person_dep)):
        return freezer.overview(svc)

    @app.post("/api/freezer")
    def api_freezer_add(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (freezer.add(svc, str(body.get("name", "")), body.get("portions") or 1,
                                          str(body.get("where", "")), str(body.get("added", "")),
                                          str(body.get("notes", "")), str(body.get("id", ""))), freezer.overview(svc))[1])

    @app.post("/api/freezer/use")
    def api_freezer_use(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: {"used": freezer.use(svc, str(body.get("id", "")), int(body.get("portions") or 1)),
                              **freezer.overview(svc)})

    @app.post("/api/freezer/remove")
    def api_freezer_remove(body: dict = BODY, person: str = Depends(person_dep)):
        freezer.remove(svc, str(body.get("id", "")))
        return freezer.overview(svc)

    # ---------- storage boxes ----------
    @app.get("/api/boxes")
    def api_boxes(q: str = "", person: str = Depends(person_dep)):
        out = {"boxes": boxes.boxes(svc), "places": boxes.PLACES}
        if q:
            out["answer"] = boxes.where_is(svc, q) or "No box on the list has that in it."
        return out

    @app.get("/api/boxes/labels")
    def api_box_labels(ids: str = "", person: str = Depends(person_dep)):
        return {"labels": boxes.labels(svc, [i for i in ids.split(",") if i] or None),
                "https": svc.settings.public_url.startswith("https://")}

    @app.get("/api/boxes/{bid}")
    def api_box(bid: str, person: str = Depends(person_dep)):
        b = boxes.get(svc, bid)
        if not b:
            raise HTTPException(404, "That box isn't on the list any more.")
        from . import files
        return {**b, "files_list": files.for_item(svc, "box", bid), "places": boxes.PLACES}

    @app.post("/api/boxes")
    def api_box_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: boxes.save(svc, str(body.get("name", "")), str(body.get("where", "")),
                                        body.get("items"), body.get("notes"), str(body.get("id", ""))))

    @app.post("/api/boxes/items")
    def api_box_items(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: boxes.add_items(svc, str(body.get("id", "")), body.get("items") or []))

    @app.post("/api/boxes/take-out")
    def api_box_take(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: boxes.take_out(svc, str(body.get("id", "")), str(body.get("item", ""))))

    @app.post("/api/boxes/remove")
    def api_box_remove(body: dict = BODY, person: str = Depends(person_dep)):
        boxes.remove(svc, str(body.get("id", "")))
        return {"ok": True}

    # ---------- guests ----------
    @app.get("/api/guests")
    def api_guests(person: str = Depends(person_dep)):
        return guests.overview(svc)

    @app.get("/api/guests/sheet")
    def api_guest_sheet(person: str = Depends(person_dep)):
        return guests.sheet(svc)

    @app.post("/api/guests/wifi")
    def api_guest_wifi(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (guests.save(svc, body.get("ssid"), body.get("password"), body.get("security"),
                                          body.get("hidden"), body.get("notes")), guests.overview(svc))[1])

    @app.post("/api/guests/visit")
    def api_guest_visit(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (guests.add_visit(svc, str(body.get("name", "")), str(body.get("arrive", "")),
                                               str(body.get("leave", "")), str(body.get("room", "")),
                                               str(body.get("notes", ""))), guests.overview(svc))[1])

    @app.post("/api/guests/{action}")
    def api_guest_do(action: str, body: dict | None = OPTIONAL_BODY, person: str = Depends(person_dep)):
        b = body or {}
        vid = str(b.get("id", ""))
        acts = {
            "tick": lambda: guests.tick(svc, vid, str(b.get("cid", "")), bool(b.get("done", True))),
            "check": lambda: guests.add_check(svc, vid, str(b.get("text", ""))),
            "remove": lambda: guests.remove_visit(svc, vid),
            "file": lambda: (d.need_google(), guests.file_jobs(svc, vid, person))[1],
            "mode": lambda: guests.set_mode(svc, int(b.get("days") or 0)),
        }
        if action not in acts:
            raise HTTPException(404, "Not Found")
        r = _call(acts[action])
        return {**guests.overview(svc), "result": r if isinstance(r, dict) and action == "file" else None}

    # ---------- something's broken ----------
    @app.get("/api/broken")
    def api_broken(q: str = "", person: str = Depends(person_dep)):
        out = {"claims": broken.claims(svc)}
        if q.strip():
            out.update(_call(lambda: broken.look(svc, q)))
        return out

    @app.post("/api/broken/claim")
    def api_broken_claim(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: broken.claim_draft(svc, person, str(body.get("name", "")), str(body.get("fault", "")),
                                                str(body.get("bought", "")), str(body.get("shop", "")),
                                                str(body.get("to", "")), str(body.get("link", ""))))

    @app.post("/api/broken/repair")
    def api_broken_repair(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: broken.repairer(svc, agent, person, str(body.get("name", "")), str(body.get("fault", ""))))

    @app.post("/api/broken/status")
    def api_broken_status(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: broken.set_status(svc, str(body.get("id", "")), str(body.get("status", ""))))

    # ---------- kids' routines (the tablet may tick) ----------
    @app.get("/api/routines")
    def api_routines(who: str = Depends(kids_dep)):
        return routines.board(svc)

    @app.post("/api/routines/tick")
    def api_routines_tick(body: dict = BODY, who: str = Depends(kids_dep)):
        return _call(lambda: routines.tick(svc, str(body.get("kid", "")), str(body.get("when", "")),
                                           int(body.get("n", -1)), bool(body.get("done", True))))

    @app.get("/api/routines/settings")
    def api_routines_cfg(person: str = Depends(person_dep)):
        return {"kids": routines.cfg(svc)}

    @app.post("/api/routines/settings")
    def api_routines_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: routines.save(svc, str(body.get("kid", "")), body.get("morning"), body.get("bedtime"),
                                           str(body.get("leave_at", "")), str(body.get("bed_at", ""))))
