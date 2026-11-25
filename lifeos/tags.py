"""NFC tag router: instant deterministic actions first, then an optional agent prompt."""
from __future__ import annotations

from .tools import Services, run_tool


def handle_tag(svc: Services, tag_id: str, who: str | None = None, agent=None) -> dict:
    tag = svc.settings.tags.get(tag_id)
    if not tag:
        svc.store.log_event("tag_unknown", tag_id)
        return {"ok": False, "error": f"unknown tag {tag_id}; add it to config/settings.yaml"}
    done, errors = [], []
    for action in tag.get("actions", []):
        (kind, params), = action.items()
        params = dict(params or {})
        try:
            if kind == "add_task":
                run_tool("add_task", {"list_name": params["list"], "title": params["title"],
                                      "notes": params.get("notes", "")}, svc)
            elif kind == "log_chore":
                from .household import chore_value
                # A tag in a child's room can name the child: {chore: Make bed, who: Ava}
                svc.store.log_chore(params["chore"], params.get("who") or who,
                                    chore_value(svc.settings, params["chore"]))
            elif kind == "ha_service":
                service = params.pop("service")
                svc.ha.call_service(service, params)  # tags are your own config, so no policy gate
            elif kind == "notify":
                svc.notifier.push(params["message"], title=params.get("title", "Life OS"))
            else:
                raise ValueError(f"unknown action {kind}")
            done.append(kind)
        except Exception as e:
            errors.append(f"{kind}: {e}")
    svc.store.log_event("tag", f"{tag_id} by {who or '?'} done={done} errors={errors}")
    reply = tag.get("say", "")
    if tag.get("agent_prompt") and agent is not None:
        reply = agent.run(tag["agent_prompt"], source=f"tag:{tag_id}") or reply
    return {"ok": not errors, "done": done, "errors": errors, "say": reply}
