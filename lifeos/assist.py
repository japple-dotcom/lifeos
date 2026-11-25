"""Life OS as a Home Assistant conversation agent, so a Home Assistant Voice speaker (or a Nest Mini with a
replacement board) talks to Life OS directly, with follow-up questions.

Two halves:
- Life OS: POST /api/voice/converse (household key) takes what was said and a conversation id, keeps the last few
  turns of that conversation for 5 minutes, answers in one to three spoken sentences, and says whether it asked a
  question back (so the speaker keeps listening). Same rules as everything else: the assistant's own safety
  policies apply, anything that spends money, sends a message, unlocks or disarms waits for a tap on a phone.
- Home Assistant: a small custom integration (download from Settings > Voice and speakers, unzip into
  config/custom_components/lifeos, restart, then Settings > Devices > Add integration > Life OS). It asks for
  Life OS's address and the household key, then appears as a conversation agent you choose in an Assist pipeline.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

from . import voice

TURN_MINUTES = 5


def converse(svc, agent, text: str, conversation_id: str = "", person: str = "", language: str = "en") -> dict:
    text = (text or "").strip()[:500]
    cid = re.sub(r"[^A-Za-z0-9_-]", "", conversation_id or "")[:64] or "ha"
    key = f"ha:{cid}"
    if not text:
        return {"say": "Sorry, I didn't catch that.", "continue": False, "conversation_id": cid}
    if agent is None or getattr(agent, "provider", None) is None:
        say = voice.quick_answer(svc, text) or "The assistant isn't connected, so I can only answer simple things."
        return {"say": say, "continue": False, "conversation_id": cid}
    history = svc.store.recent_turns(key, max_turns=6, idle_minutes=TURN_MINUTES)
    quick = "" if history else voice.quick_answer(svc, text)
    if quick:
        reply = quick
    else:
        try:
            reply = agent.run(f"{voice.VOICE_RULES} If you need one detail to finish the job, ask one short question."
                              f"\n\nSaid to a speaker by {person or 'someone in the house'}: {text}",
                              source="voice", history=history)
        except Exception as e:
            from . import aierrors
            return {"say": voice.clean_for_speech("Sorry. " + aierrors.explain(e, agent.active_provider_name()), 300),
                    "continue": False, "conversation_id": cid}
    say = voice.clean_for_speech(reply or "Done.", 400)
    svc.store.add_turn(key, "user", text)
    svc.store.add_turn(key, "assistant", say)
    return {"say": say, "continue": say.rstrip().endswith("?"), "conversation_id": cid}


# ---------- the Home Assistant integration (written out as files on request) ----------
MANIFEST = {
    "domain": "lifeos", "name": "Life OS", "version": "{version}", "config_flow": True,
    "dependencies": ["conversation"], "documentation": "https://github.com/japple-dotcom/lifeos",
    "iot_class": "local_polling", "requirements": [], "codeowners": [], "integration_type": "service",
}

INIT_PY = '''"""Life OS for Home Assistant: a conversation agent that passes what's said to Life OS."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
'''

CONFIG_FLOW_PY = '''"""Asks for Life OS's address and the household key, and checks them."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

DOMAIN = "lifeos"
SCHEMA = vol.Schema({
    vol.Required("url", default="http://192.168.1.10:8800"): str,
    vol.Required("key"): str,
})


class LifeOSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            url = user_input["url"].rstrip("/")
            try:
                session = async_get_clientsession(self.hass)
                async with session.get(f"{url}/provider", headers={"X-LifeOS-Key": user_input["key"].strip()},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 401:
                        errors["key"] = "bad_key"
                    elif r.status != 200:
                        errors["url"] = "cannot_connect"
            except Exception:
                errors["url"] = "cannot_connect"
            if not errors:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Life OS", data={"url": url, "key": user_input["key"].strip()})
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
'''

CONVERSATION_PY = '''"""The Life OS conversation agent: what's said goes to Life OS, its answer is spoken, and when Life OS asks a
question back the speaker keeps listening for the answer."""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LifeOSAgent(entry)])


class LifeOSAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {"identifiers": {("lifeos", entry.entry_id)}, "name": "Life OS",
                                  "manufacturer": "Life OS", "entry_type": "service"}

    @property
    def supported_languages(self):
        return MATCH_ALL

    async def _ask(self, text: str, conversation_id: str, language: str) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(f"{self.entry.data['url']}/api/voice/converse",
                                    headers={"X-LifeOS-Key": self.entry.data["key"]},
                                    json={"text": text, "conversation_id": conversation_id, "language": language},
                                    timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    _LOGGER.warning("Life OS answered %s", r.status)
                    return {"say": "Life OS didn't answer properly. Check it's running.", "continue": False}
                return await r.json()
        except Exception as e:  # Life OS asleep, moved, or the network's down
            _LOGGER.warning("Life OS not reachable: %s", e)
            return {"say": "I can't reach Life OS just now.", "continue": False}

    async def _async_handle_message(self, user_input, chat_log):
        answer = await self._ask(user_input.text, chat_log.conversation_id, user_input.language)
        say = answer.get("say") or "Done."
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=say))
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(say)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id,
                                               continue_conversation=bool(answer.get("continue")))
'''

STRINGS = {
    "config": {
        "step": {"user": {"title": "Connect to Life OS",
                          "description": "Life OS's address as this Home Assistant can reach it (for example "
                                         "http://192.168.1.10:8800, or your Tailscale https address), and the household key.",
                          "data": {"url": "Life OS address", "key": "Household key"}}},
        "error": {"cannot_connect": "Couldn't reach Life OS at that address.", "bad_key": "That household key isn't right."},
        "abort": {"already_configured": "Life OS is already connected."},
    }
}

README = """Life OS for Home Assistant
==========================

1. Copy this "lifeos" folder into Home Assistant's config/custom_components/ folder
   (so you have config/custom_components/lifeos/manifest.json). The File editor or Samba add-on makes this easy.
2. Restart Home Assistant.
3. Settings > Devices & services > Add integration > Life OS. Give Life OS's address and the household key.
4. Settings > Voice assistants > your assistant > Conversation agent: choose Life OS.
   Tick "Prefer handling commands locally" if you want Home Assistant to switch lights itself first.

Then talk to your Home Assistant Voice speaker as normal. When Life OS asks a question back, the speaker keeps
listening for your answer. Anything that spends money, sends a message, unlocks or disarms still waits for a tap
on a phone.
"""


def integration_files(version: str) -> dict[str, str]:
    manifest = {**MANIFEST, "version": version}
    return {
        "lifeos/manifest.json": json.dumps(manifest, indent=2) + "\n",
        "lifeos/__init__.py": INIT_PY,
        "lifeos/config_flow.py": CONFIG_FLOW_PY,
        "lifeos/conversation.py": CONVERSATION_PY,
        "lifeos/strings.json": json.dumps(STRINGS, indent=2) + "\n",
        "lifeos/translations/en.json": json.dumps(STRINGS, indent=2) + "\n",
        "lifeos/README.txt": README,
    }


def integration_zip(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in integration_files(version).items():
            z.writestr(name, body)
    return buf.getvalue()
