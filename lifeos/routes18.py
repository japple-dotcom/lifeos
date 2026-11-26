"""Routes for the v0.18 features (thin: the work is in each feature's own module).

Deliveries, bin days, the babysitter sheet, date night, the kids' reading/homework/stars, the cleaning rota,
car running costs, the live test, the Home Assistant conversation agent, and the household key page that only the
menu bar icon on this computer can open.
"""
from __future__ import annotations

import html
import logging
from types import SimpleNamespace

from fastapi import Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from . import (assist, bins, carcosts, cleaning, datenight, deliveries, kidstuff, livetest, sitter)

log = logging.getLogger("lifeos.routes18")
BODY = Body(...)
OPTIONAL_BODY = Body(default=None)


def _bad(e: Exception):
    raise HTTPException(400, str(e)) from e


def _call(fn):
    try:
        return fn()
    except (ValueError, KeyError, TypeError) as e:
        _bad(ValueError(str(e)))


def add_routes(app, svc, agent, d: SimpleNamespace) -> None:
    s = svc.settings
    person_dep, kids_dep, key_dep = d.current_person, d.current_kids, d.require_key

    # ---------- deliveries ----------
    @app.get("/api/deliveries")
    def api_deliveries(person: str = Depends(person_dep)):
        return deliveries.current(svc)

    @app.post("/api/deliveries/mark")
    def api_delivery_mark(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: deliveries.mark(svc, str(body.get("id", "")), str(body.get("status", "")),
                                             bool(body.get("hide"))))

    @app.post("/api/deliveries/check")
    def api_delivery_check(person: str = Depends(person_dep)):
        d.need_google()
        return {"found": deliveries.read_emails(svc, agent), **deliveries.current(svc)}

    # ---------- bin days ----------
    @app.get("/api/bins")
    def api_bins(person: str = Depends(person_dep)):
        return bins.overview(svc)

    @app.post("/api/bins")
    def api_bins_save(body: dict = BODY, person: str = Depends(person_dep)):
        rota = body.get("rota")
        return _call(lambda: (bins.save(svc, calendar=body.get("calendar"), rota=rota if isinstance(rota, list) else None,
                                        remind_at=body.get("remind_at"), speak=body.get("speak")), bins.overview(svc))[1])

    # ---------- babysitter sheet ----------
    @app.get("/api/sitter")
    def api_sitter(person: str = Depends(person_dep)):
        return sitter.sheet(svc)

    @app.post("/api/sitter")
    def api_sitter_save(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: (sitter.save(svc, body), sitter.sheet(svc))[1])

    # ---------- date night ----------
    @app.get("/api/date-night")
    def api_date_night(person: str = Depends(person_dep)):
        nights = []
        if svc.google:
            try:
                nights = datenight.evenings(svc)
            except Exception as e:
                log.warning("date night evenings: %s", e)
        return {"evenings": nights, "sitters": datenight.sitters(svc), "booked": datenight.booked(svc),
                "google": bool(svc.google), "area": s.household.get("area") or ""}

    @app.get("/api/date-night/ideas")
    def api_date_ideas(fresh: bool = False, person: str = Depends(person_dep)):
        return datenight.ideas(svc, agent, fresh=fresh)

    @app.post("/api/date-night/message")
    def api_date_message(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: {"text": datenight.message(svc, str(body.get("date", "")), str(body.get("name", "")),
                                                        str(body.get("start", "")), str(body.get("end", "")), d.name_of(person))})

    @app.post("/api/date-night/draft")
    def api_date_draft(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: datenight.draft(svc, str(body.get("date", "")), int(body.get("contact_id") or 0), person,
                                             str(body.get("start", "")), str(body.get("end", ""))))

    @app.post("/api/date-night/book")
    def api_date_book(body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: datenight.book(svc, str(body.get("date", "")), str(body.get("start", "")),
                                            str(body.get("end", "")), person))

    # ---------- kids: reading, homework, stars (the tablet may log reading and tick homework) ----------
    @app.get("/api/kids/extras")
    def api_kids_extras(who: str = Depends(kids_dep)):
        return kidstuff.board(svc)

    @app.post("/api/kids/reading")
    def api_kids_reading(body: dict = BODY, who: str = Depends(kids_dep)):
        return _call(lambda: kidstuff.log_reading(svc, str(body.get("kid", "")), int(body.get("minutes") or 0),
                                                  str(body.get("book", "")), who, str(body.get("date", ""))))

    @app.post("/api/kids/homework/done")
    def api_kids_homework_done(body: dict = BODY, who: str = Depends(kids_dep)):
        return _call(lambda: kidstuff.tick_homework(svc, str(body.get("id", "")), who))

    @app.post("/api/kids/extras/undo")
    def api_kids_extras_undo(body: dict = BODY, who: str = Depends(kids_dep)):
        return _call(lambda: {"ok": kidstuff.undo(svc, str(body.get("kind", "")), str(body.get("id", "")),
                                                  tablet=who == "kids")})

    @app.post("/api/kids/homework")
    def api_kids_homework_add(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: kidstuff.add_homework(svc, str(body.get("kid", "")), str(body.get("title", "")),
                                                   str(body.get("due", "")), person))

    @app.post("/api/kids/homework/remove")
    def api_kids_homework_remove(body: dict = BODY, person: str = Depends(person_dep)):
        return {"ok": kidstuff.remove_homework(svc, str(body.get("id", "")))}

    @app.post("/api/kids/reading/remove")
    def api_kids_reading_remove(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: {"ok": kidstuff.undo(svc, "reading", str(body.get("id", "")), tablet=False)})

    # ---------- cleaning rota and the robot vacuum ----------
    @app.get("/api/cleaning")
    def api_cleaning(person: str = Depends(person_dep)):
        return {**cleaning.week(svc), "settings": cleaning.settings_view(svc)}

    @app.post("/api/cleaning/done")
    def api_cleaning_done(body: dict = BODY, person: str = Depends(person_dep)):
        by = str(body.get("by") or person)
        return _call(lambda: (cleaning.tick(svc, str(body.get("id", "")), by), cleaning.week(svc))[1])

    @app.post("/api/cleaning/undo")
    def api_cleaning_undo(body: dict = BODY, person: str = Depends(person_dep)):
        cleaning.untick(svc, str(body.get("id", "")))
        return cleaning.week(svc)

    @app.post("/api/cleaning/swap")
    def api_cleaning_swap(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: cleaning.swap(svc, str(body.get("id", ""))))

    @app.post("/api/cleaning/todos")
    def api_cleaning_todos(person: str = Depends(person_dep)):
        d.need_google()
        return cleaning.file_todos(svc)

    @app.post("/api/cleaning/settings")
    def api_cleaning_settings(body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: cleaning.save_settings(svc, body.get("off"), body.get("vacuum")))

    # ---------- car running costs ----------
    @app.get("/api/cars/{reg}/costs")
    def api_car_costs(reg: str, person: str = Depends(person_dep)):
        return _call(lambda: {**carcosts.summary(svc, reg), "kinds": carcosts.KINDS})

    @app.post("/api/cars/{reg}/costs")
    def api_car_cost_add(reg: str, body: dict = BODY, person: str = Depends(person_dep)):
        def num(k, cast=float):
            v = body.get(k)
            return cast(v) if v not in (None, "") else None
        return _call(lambda: (carcosts.add_cost(svc, reg, str(body.get("kind", "")), float(body.get("amount") or 0),
                                                str(body.get("date", "")), num("miles", int), num("litres"), num("kwh"),
                                                str(body.get("note", ""))), carcosts.summary(svc, reg))[1])

    @app.post("/api/cars/{reg}/costs/remove")
    def api_car_cost_remove(reg: str, body: dict = BODY, person: str = Depends(person_dep)):
        carcosts.remove_cost(svc, str(body.get("id", "")))
        return _call(lambda: carcosts.summary(svc, reg))

    @app.post("/api/cars/{reg}/service")
    def api_car_service(reg: str, body: dict = BODY, person: str = Depends(person_dep)):
        def num(k):
            v = body.get(k)
            return int(v) if v not in (None, "") else None
        return _call(lambda: carcosts.set_service(svc, reg, num("every_miles"), num("every_months"),
                                                  str(body.get("last_date", "")), num("last_miles")))

    @app.post("/api/cars/{reg}/advisories")
    def api_car_advisory(reg: str, body: dict = BODY, person: str = Depends(person_dep)):
        return _call(lambda: carcosts.add_advisory(svc, reg, str(body.get("text", ""))))

    @app.post("/api/cars/{reg}/advisories/todo")
    def api_car_advisory_todo(reg: str, body: dict = BODY, person: str = Depends(person_dep)):
        d.need_google()
        return _call(lambda: carcosts.advisory_todo(svc, reg, str(body.get("id", "")), person))

    @app.post("/api/cars/{reg}/mot")
    def api_car_mot(reg: str, person: str = Depends(person_dep)):
        try:
            return carcosts.fetch_mot(svc, reg)
        except ValueError as e:
            _bad(e)
        except Exception as e:
            log.warning("MOT history: %s", e)
            raise HTTPException(502, "DVSA's MOT history service didn't answer. Check the keys, or try later.") from e

    # ---------- live test ----------
    @app.get("/api/system/live-test")
    def api_live_last(person: str = Depends(person_dep)):
        r = livetest.last(svc)
        return {"report": r, "text": livetest.text(r) if r else ""}

    @app.post("/api/system/live-test")
    def api_live_run(body: dict | None = OPTIONAL_BODY, person: str = Depends(person_dep)):
        r = livetest.run(svc, agent, ask_ai=bool((body or {}).get("ai")))
        return {"report": r, "text": livetest.text(r)}

    # ---------- Home Assistant conversation agent ----------
    @app.post("/api/voice/converse", dependencies=[Depends(key_dep)])
    def api_voice_converse(body: dict = BODY):
        return assist.converse(svc, agent, str(body.get("text", "")), str(body.get("conversation_id", "")),
                               str(body.get("person", "")), str(body.get("language", "en")))

    @app.get("/api/voice/integration.zip")
    def api_voice_integration(person: str = Depends(person_dep)):
        from . import __version__
        return Response(assist.integration_zip(__version__), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="lifeos-home-assistant.zip"'})

    # ---------- the household key, on this computer only (from the menu bar icon) ----------
    @app.get("/local/key", include_in_schema=False)
    def local_key(request: Request, t: str = ""):
        from . import desktop
        ctl = desktop.controller
        from .localonly import is_local
        if not ctl or not ctl.key_token_ok(t) or not s.api_key or not is_local(request):
            raise HTTPException(404, "Not Found")
        key = html.escape(s.api_key)
        from . import phones, qr
        codes = ""
        if s.public_url.startswith("https://"):
            names = s.household.get("names") or {}
            cards = []
            for p in s.household.get("adults") or []:
                name = html.escape(names.get(p, p.title()))
                link = phones.join_link(svc, phones.make_code(p))
                cards.append(f'<figure>{qr.svg(link, 220, f"Sign in as {name}")}<figcaption>{name}\'s phone</figcaption></figure>')
            codes = ('<h2>Or scan with the phone\'s camera</h2><p>Each code signs one phone in as that person, once, '
                     'within 10 minutes. The phone needs Tailscale switched on.</p><div class="codes">' + "".join(cards) + "</div>")
        else:
            codes = ("<p class='hint'>To sign phones in by scanning a code, set up phone access first (Settings &gt; "
                     "Updates and this computer &gt; Set up phone access).</p>")
        page = f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Life OS household key</title>
<style>body{{font:18px/1.5 system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem;color:#111;background:#f6f5f1}}
code{{display:block;font-size:1.6rem;padding:1rem;margin:1rem 0;background:#fff;border:2px solid #0a5c53;border-radius:12px;
word-break:break-all;user-select:all}}p.hint{{color:#444}}.codes{{display:flex;flex-wrap:wrap;gap:1.5rem}}
figure{{margin:0;text-align:center}}figure svg{{border-radius:8px}}@media (prefers-color-scheme:dark){{body{{background:#101214;color:#eee}}
code{{background:#1b1e21;border-color:#4fc1b0}}p.hint{{color:#bbb}}}}</style></head><body><main>
<h1>Your household key</h1><p>Type this on a phone or the kitchen tablet to sign in to Life OS once.</p>
<code id="k">{key}</code>
{codes}
<p class="hint">This page only opens from the Life OS icon in the menu bar on this computer, and stops working after 10
minutes. Keep the key to your household.</p></main></body></html>"""
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})
