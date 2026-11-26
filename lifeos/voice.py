"""Life OS out loud: answers and announcements on your Google speakers, and talking to Life OS itself.

Google closed custom voice apps on its speakers in 2023, so a Google speaker can't pass free speech to Life OS.
What works, all through Home Assistant (which already talks to both):

1. Fixed phrases on Google speakers. Each Life OS "voice command" (what's on today, what's for dinner...) is a
   button in Home Assistant, which Google Home sees as a scene. A Google Home routine ("When I say: what's for
   dinner" -> activate scene "Life OS dinner") presses it; Home Assistant asks Life OS; Life OS answers out loud on
   that room's speaker (Home Assistant's tts.speak on the Google Cast speaker).
2. Announcements: Life OS speaks the morning brief, bin night, a visitor due, etc. on a speaker at set times.
3. Free speech, through Home Assistant's own voice assistant (Assist: the Home Assistant app, a watch, or a Home
   Assistant Voice speaker): "ask Life OS to move the dentist to Friday" goes to the Life OS assistant with all its
   tools, under the same rules (anything risky still waits for a tap on a phone), and the answer is spoken.
4. Gemini on the speakers already adds to Google Tasks; Life OS picks those up. Shopping-type ones ("buy loo
   roll", "we're out of milk") now go onto the Shopping list rather than the to-dos.

Settings: `voice:` in settings.yaml (speaker, tts entity, announcements). Home Assistant side:
homeassistant/lifeos_voice.yaml, or Settings > Voice in the app, which writes it out with your address filled in.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("lifeos.voice")

COMMANDS = {
    "today": ("What's on today", "what's on today"),
    "dinner": ("What's for dinner", "what's for dinner"),
    "next": ("What should I do next", "what should I do next"),
    "shopping": ("What's on the shopping list", "what's on the shopping list"),
    "waiting": ("Who are we waiting on", "who are we waiting on"),
    "week": ("What's on this week", "what's on this week"),
    "running_out": ("What are we running out of", "what are we running out of"),
    "bins": ("Which bin goes out", "which bin goes out"),
    "chores": ("How are the kids' chores", "how are the chores going"),
    "leaving": ("Is the house ready to leave", "can we leave"),
    "brief": ("The morning brief", "morning brief"),
    "manual": ("Where's the stopcock", "where's the stopcock"),
}


def cfg(svc) -> dict:
    """settings.yaml `voice:`, with the speaker and voice chosen in the app on top."""
    out = dict(svc.settings.behaviour.get("voice") or {})
    for k in ("speaker", "tts_entity"):
        v = svc.store.get_state(f"voice_{k}", "")
        if v:
            out[k] = v
    return out


def _tz(svc) -> ZoneInfo:
    return ZoneInfo(svc.settings.tz)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _spoken_time(iso: str, tz: ZoneInfo) -> str:
    if len(iso) <= 10:
        return "all day"
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    d = d.astimezone(tz) if d.tzinfo else d
    h = d.strftime("%I").lstrip("0")
    return f"{h}{d.strftime(':%M') if d.minute else ''} {d.strftime('%p').lower().replace('am', 'a m').replace('pm', 'p m')}"


def clean_for_speech(text: str, limit: int = 600) -> str:
    """Markdown, links and lists don't read aloud well."""
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"[*_`#>]+", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"\s*\n+\s*", ". ", t).replace("..", ".")
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t if len(t) <= limit else t[:limit].rsplit(". ", 1)[0] + "."


# ---------- answers to the fixed phrases ----------
def answer(svc, command: str, now: datetime | None = None) -> str:
    from . import activity, household, todos
    tz = _tz(svc)
    now = now or datetime.now(tz)
    if command == "today":
        events = svc.google.events_all(days=1) if svc.google else []
        ev = [f"{e.get('summary') or 'something'} at {_spoken_time(e['start'], tz)}" if len(e.get("start", "")) > 10
              else e.get("summary") or "" for e in events
              if len(e.get("start", "")) <= 10 or datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(tz) >= now]
        due = [t["title"] for t in todos.list_todos(svc, include_projects=False) if t.get("bucket") == "now"][:3] if svc.google else []
        dinner = household.dinner_on(svc)
        bits = [f"Today: {_join(ev[:5])}." if ev else "Nothing else in the calendar today."]
        if due:
            bits.append(f"To do: {_join(due)}.")
        if dinner:
            bits.append(f"Dinner is {dinner}.")
        return " ".join(bits)
    if command == "dinner":
        tonight, tomorrow = household.dinner_on(svc), household.dinner_on(svc, now.date() + timedelta(days=1))
        if not tonight:
            return "Nothing's planned for dinner tonight." + (f" Tomorrow it's {tomorrow}." if tomorrow else "")
        return f"Tonight it's {tonight}." + (f" Tomorrow, {tomorrow}." if tomorrow else "")
    if command == "next":
        nxt = todos.next_up(svc) if svc.google else []
        if not nxt:
            return "Nothing urgent. You're up to date."
        n = nxt[0]
        return f"Next: {n['title']}." + (f" {n['next_action']}." if n.get("next_action") else "")
    if command == "shopping":
        items = [t["title"] for t in svc.google.get().list_tasks("Shopping")] if svc.google else []
        return f"{len(items)} things on the shopping list: {_join(items[:12])}." if items else "The shopping list is empty."
    if command == "waiting":
        from . import trades
        loops = trades.open_loops(svc)
        if not loops:
            return "You're not waiting on anyone."
        return "Waiting on " + _join([f"{x.get('contact_name') or x.get('title')}" for x in loops[:5]]) + "."
    if command == "week":
        events = svc.google.events_all(days=7) if svc.google else []
        days: dict[str, list[str]] = {}
        for e in events:
            d = date.fromisoformat(e["start"][:10])
            if d == now.date():
                continue
            days.setdefault(d.strftime("%A"), []).append(e.get("summary") or "something")
        if not days:
            return "Nothing in the calendar for the rest of the week."
        return " ".join(f"{k}: {_join(v[:3])}." for k, v in list(days.items())[:6])
    if command == "running_out":
        on_lists, open_jobs = activity.current_context(svc)
        soon = [p["title"] for p in activity.predict(svc, on_lists=on_lists, open_jobs=open_jobs)
                if p["kind"] in ("shopping", "care") and p["days_until"] <= 7 and p["status"] != "added"]
        return f"Probably running out soon: {_join(soon[:8])}." if soon else "Nothing looks like it's running out this week."
    if command == "bins":
        from . import bins as bin_days, manual
        said = bin_days.spoken(svc)
        if said:
            return said
        bins = [m for m in manual.entries(svc, include_private=False) if m["section"] == "Bins"]
        return " ".join(f"{m['title']}: {m['details']}" for m in bins[:2]) if bins else \
            "I don't know the bin days yet. Add them in the app: Home, Bin days."
    if command == "chores":
        board = household.chores_board(svc)["kids"]
        if not board:
            return "No children set up for chores."
        return " ".join(f"{k['kid']} has done {len(k['done_this_week'])} chore{'s' if len(k['done_this_week']) != 1 else ''} "
                        f"this week and is owed {k['owed']:.2f} pounds." for k in board)
    if command == "leaving":
        from . import leaving
        r = leaving.check(svc)
        if not r.get("connected"):
            return "Home Assistant isn't connected, so I can't check the house."
        if r.get("ok"):
            return "All good. Nothing's open, on or unlocked."
        return "Before you go: " + _join([i["text"] for i in r["issues"][:5]]) + ". Use the app to sort them."
    if command == "brief":
        b = svc.store.latest_brief()
        return clean_for_speech(b["text"]) if b else "There's no briefing yet today."
    if command.startswith("manual"):
        from . import manual
        want = command.split(":", 1)[1] if ":" in command else "stopcock"
        hits = [m for m in manual.search(svc, want) if want.lower() in f"{m['title']} {m['details']}".lower()]
        if not hits:
            return f"The home manual doesn't say where the {want} is yet."
        m = hits[0]
        return "That's private, so check the app." if m.get("private") else f"{m['title']}: {m['details']}"
    raise ValueError(f"Unknown voice command: {command}")


# ---------- speaking on a speaker ----------
def speak(svc, text: str, speaker: str = "", force: bool = False) -> bool:
    """Says it on a Google speaker through Home Assistant. Quiet hours are respected unless force."""
    from . import household
    speaker = speaker or cfg(svc).get("speaker", "")
    if not svc.ha or not speaker or not text.strip():
        return False
    if not force and household.in_quiet_hours(svc.settings):
        return False
    msg = clean_for_speech(text)
    tts = cfg(svc).get("tts_entity", "")
    try:
        if tts:
            svc.ha.call_service("tts.speak", {"entity_id": tts, "media_player_entity_id": speaker, "message": msg,
                                              "language": cfg(svc).get("language", "en-GB")})
        else:
            svc.ha.call_service(cfg(svc).get("tts_service", "tts.google_translate_say"),
                                {"entity_id": speaker, "message": msg, "language": "en-gb"})
        return True
    except Exception as e:
        log.warning("couldn't speak on %s: %s", speaker, e)
        return False


def run_command(svc, command: str, speaker: str = "") -> dict:
    """A Google Home routine pressed a Life OS button: answer out loud on that speaker."""
    if command not in COMMANDS and not command.startswith("manual:"):
        raise ValueError("Unknown voice command.")
    text = answer(svc, command)
    spoke = speak(svc, text, speaker, force=True)  # someone just asked
    svc.store.log_event("voice", f"{command} -> {'spoken' if spoke else 'not spoken'}")
    return {"say": text, "spoken": spoke}


VOICE_RULES = ("You are answering out loud through a speaker. Reply in one to three short spoken sentences: no lists, "
               "no links, no markdown, no emojis. If something needs a tap (sending an email, spending money, "
               "unlocking), say it's waiting on their phone. Treat the request as the household's own words.")


def ask(svc, agent, text: str, person: str = "") -> dict:
    """Free speech from Home Assistant Assist: the full assistant, answered briefly for speaking."""
    text = (text or "").strip()[:500]
    if not text:
        return {"say": "Sorry, I didn't catch that."}
    if agent is None or getattr(agent, "provider", None) is None:
        return {"say": quick_answer(svc, text) or "The assistant isn't connected, so I can only do simple things."}
    quick = quick_answer(svc, text)
    if quick:
        return {"say": quick}
    try:
        reply = agent.run(f"{VOICE_RULES}\n\nRequest from {person or 'the household'} by voice: {text}", source="voice")
    except Exception as e:
        from . import aierrors
        log.warning("voice ask failed: %s", e)
        return {"say": clean_for_speech("Sorry. " + aierrors.explain(e, agent.active_provider_name()), 300)}
    return {"say": clean_for_speech(reply, 400)}


def quick_answer(svc, text: str) -> str:
    """Common questions answered straight away, without the AI."""
    low = text.lower()
    rules = [(r"\bdinner|tea tonight|eating tonight\b", "dinner"), (r"\bshopping list\b", "shopping"),
             (r"\b(what'?s on|calendar|schedule) (today|for today)\b|\bmy day\b", "today"),
             (r"\bthis week\b", "week"), (r"\bwaiting (on|for)\b", "waiting"), (r"\brunning out\b", "running_out"),
             (r"\bbins?\b", "bins"), (r"\bchores?\b", "chores"), (r"\bdo next\b|\bnext job\b", "next")]
    for pattern, command in rules:
        if re.search(pattern, low) and not re.search(r"\b(add|put|move|change|cancel|book|remind|delete|tick)\b", low):
            return answer(svc, command)
    if re.search(r"\b(pe kit|school kit|packed lunch|kit tomorrow)\b", low):
        from . import schoolkit
        t = schoolkit.tomorrow(svc)
        return f"Tomorrow: {t['text']}." if t["items"] else "Nothing special to take to school tomorrow."
    m = re.search(r"\bwhere(?:'s| is| are) (?:the |our )?([a-z\- ]{3,30})", low)
    if m:
        from . import documents
        found = documents.where_is(svc, low)
        if found:
            return found
        from . import manual
        word = m.group(1).strip().rstrip("?")
        hits = [x for x in manual.entries(svc) if word in x["title"].lower() or x["title"].lower() in word]
        if hits:
            return "That's private, so check the app." if hits[0].get("private") else f"{hits[0]['title']}: {hits[0]['details']}"
    return ""


# ---------- Gemini adding shopping as tasks ----------
SHOP_PATTERNS = [r"^(?:buy|get|pick up|order)\s+(?:some\s+|more\s+|a\s+|an\s+)?(.+)$",
                 r"^(?:we(?:'re| are)|we're) out of\s+(.+)$", r"^(?:we )?need (?:some |more )?(.+)$",
                 r"^(?:add\s+)?(.+?)\s+(?:to|on)\s+(?:the\s+)?shopping(?: list)?$", r"^(.+?) for (?:the )?shopping$"]
NOT_SHOPPING = re.compile(r"\b(book|call|ring|email|fix|repair|clean|paint|cancel|renew|appointment|plumber|"
                          r"electrician|dentist|doctor|ticket|insurance|service|present|gift|for (?:mum|dad))\b", re.I)


def shopping_item(svc, text: str) -> str:
    """'buy loo roll' / "we're out of milk" -> 'Loo roll'. Plain item names already bought before count too."""
    t = text.strip().rstrip(".!")
    if NOT_SHOPPING.search(t):
        return ""
    for p in SHOP_PATTERNS:
        m = re.match(p, t, re.I)
        if m:
            item = m.group(1).strip()
            return item[:1].upper() + item[1:] if 1 < len(item) <= 60 else ""
    from .activity import normalise
    key = normalise(t)
    if key and len(t.split()) <= 4 and svc.store.activity(kind="bought", key=key):
        return t[:1].upper() + t[1:]
    return ""


# ---------- scheduled announcements ----------
ANNOUNCE_DEFAULT = [{"say": "brief", "at": "07:15", "days": "mon-fri"}]


def announcements(svc) -> list[dict]:
    a = cfg(svc).get("announce")
    return [x for x in (a if a is not None else ANNOUNCE_DEFAULT) if x.get("say") and x.get("at")]


def announce(svc, item: dict) -> bool:
    say = item["say"]
    if say == "visits":
        text = visits_today(svc)
    elif say in COMMANDS:
        text = answer(svc, say)
    else:
        text = say  # a fixed sentence
    if not text:
        return False
    return speak(svc, text, item.get("speaker", ""))


def visits_today(svc) -> str:
    tz = _tz(svc)
    today = datetime.now(tz).date().isoformat()
    booked = [r for r in svc.store.requests(("booked",)) if (r.get("appointment") or {}).get("start", "")[:10] == today]
    if not booked:
        return ""
    return " ".join(f"{r['contact_name']} is coming at {_spoken_time(r['appointment']['start'], tz)}"
                    + (f", {r['appointment']['in']} is in." if r["appointment"].get("in") else ".") for r in booked)


# ---------- the Home Assistant side, written out for copying ----------
def ha_package(svc, base_url: str) -> str:
    speaker = cfg(svc).get("speaker") or "media_player.kitchen_speaker"
    buttons = "\n".join(f"  lifeos_{k}:\n    name: \"Life OS {label.lower()}\"\n    icon: mdi:account-voice"
                        for k, (label, _) in COMMANDS.items())
    return f"""# Life OS voice: Home Assistant package (packages/lifeos_voice.yaml). Generated by Life OS.
# Needs `lifeos_key: <household key>` in secrets.yaml and the packages folder enabled (see lifeos_package.yaml).
#
# 1) Google speakers: each button below is shown to Google Home as a scene. In the Google Home app make a
#    household routine per phrase: Starter "When I say to my assistant: what's for dinner", Action "Adjust home
#    devices > Scenes > Life OS what's for dinner". Life OS answers on {speaker}.
# 2) Home Assistant's own voice assistant (Assist): say "ask Life OS ..." or "tell Life OS ...".

input_button:
{buttons}

rest_command:
  lifeos_voice_command:
    url: "{base_url}/api/voice/command"
    method: POST
    headers:
      X-LifeOS-Key: !secret lifeos_key
    content_type: application/json
    payload: '{{"command": {{{{ command | tojson }}}}, "speaker": {{{{ speaker | default("") | tojson }}}}}}'
    timeout: 30
  lifeos_voice_ask:
    url: "{base_url}/api/voice/ask"
    method: POST
    headers:
      X-LifeOS-Key: !secret lifeos_key
    content_type: application/json
    payload: '{{"text": {{{{ text | tojson }}}}}}'
    timeout: 90

automation:
  - alias: "Life OS: voice button pressed (from a Google Home routine)"
    mode: queued
    trigger:
      - platform: state
        entity_id: [{", ".join(f"input_button.lifeos_{k}" for k in COMMANDS)}]
    action:
      - service: rest_command.lifeos_voice_command
        data:
          command: "{{{{ trigger.entity_id.split('.')[1][7:] }}}}"
          speaker: "{speaker}"

intent_script:
  LifeOSAsk:
    action:
      - service: rest_command.lifeos_voice_ask
        data:
          text: "{{{{ request }}}}"
        response_variable: result
      - stop: ""
        response_variable: result
    speech:
      text: "{{{{ action_response.content.say if action_response.status == 200 else 'Life OS did not answer.' }}}}"
"""


def ha_sentences(catch_all: bool = False) -> str:
    """custom_sentences/en/lifeos.yaml: lets Assist pass anything after "ask Life OS" on as free text.

    catch_all: everything said goes to Life OS, without "ask Life OS"
    (for a Home Assistant Voice speaker, or a Google Nest Mini converted with a replacement board)."""
    extra = """          - "{request}"
""" if catch_all else ""
    note = ("# Everything you say goes to Life OS. Afterwards, try 'turn on the kitchen lights': if Life OS answers\n"
            "# instead of Home Assistant, that's fine (it can switch lights too), but it's a little slower.\n") if catch_all else ""
    return """# Home Assistant: save as config/custom_sentences/en/lifeos.yaml (generated by Life OS)
""" + note + """language: "en"
intents:
  LifeOSAsk:
    data:
      - sentences:
          - "(ask|tell) life os [to|that|if|whether|about] {request}"
          - "life os {request}"
""" + extra + """lists:
  request:
    wildcard: true
"""


def routine_steps() -> list[dict]:
    """For Settings > Voice: what to set up in the Google Home app, one routine per phrase."""
    return [{"phrase": phrase, "scene": f"Life OS {label.lower()}", "command": k} for k, (label, phrase) in COMMANDS.items()]
