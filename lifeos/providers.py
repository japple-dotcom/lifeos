"""AI providers. Exactly one is active at a time (LIFEOS_PROVIDER, or switched live via POST /provider).

Each provider runs the same conversation: a system prompt, the same tool list, a user prompt, and a
callback that executes a tool call (through the shared safety policy) and returns (content, is_error).
"""
from __future__ import annotations

import json
from collections.abc import Callable

ToolCallback = Callable[[str, dict], "tuple[str, bool]"]
MAX_STEPS = 15
STOPPED = "Stopped after too many steps; check the event log."


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str = "", model: str = "", model_deep: str = "", client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client, self.model, self.model_deep = client, model, model_deep

    @staticmethod
    def _block(b) -> dict:
        t = getattr(b, "type", None)
        if t == "text":
            return {"type": "text", "text": b.text}
        if t == "tool_use":
            return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        return b.model_dump() if hasattr(b, "model_dump") else dict(b.__dict__)

    supports_web_search = True

    def chat(self, system: str, tools: list[dict], prompt: str, on_tool: ToolCallback, deep: bool = False,
             history: list[dict] | None = None, web_search: bool = False,
             image: tuple[bytes, str] | None = None) -> str:
        messages: list[dict] = [{"role": "user" if h["role"] == "user" else "assistant", "content": h["text"]}
                                for h in (history or [])]
        if image:  # (bytes, mime type): only the fridge photo, only when someone taps
            import base64
            messages.append({"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": image[1],
                                             "data": base64.b64encode(image[0]).decode()}},
                {"type": "text", "text": prompt}]})
        else:
            messages.append({"role": "user", "content": prompt})
        all_tools = list(tools)
        if web_search:  # Claude searches the web itself (server-side); results come back with real links
            all_tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5,
                              "user_location": {"type": "approximate", "country": "GB",
                                                "timezone": "Europe/London"}})
        for _ in range(MAX_STEPS):
            resp = self.client.messages.create(
                model=self.model_deep if deep else self.model, max_tokens=4096,
                system=system, tools=all_tools, messages=messages,
            )
            content = [self._block(b) for b in resp.content]
            # The API needs its own blocks back exactly as sent (search results carry encrypted content).
            messages.append({"role": "assistant", "content": list(resp.content) if web_search else content})
            if resp.stop_reason == "pause_turn":  # a long web search paused; send it back to carry on
                continue
            if resp.stop_reason != "tool_use":
                return "\n".join(b["text"] for b in content if b["type"] == "text").strip()
            results = []
            for b in content:
                if b["type"] == "tool_use":
                    out, err = on_tool(b["name"], b["input"] or {})
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        return STOPPED


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str = "", model: str = "", model_deep: str = "", client=None):
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client, self.model, self.model_deep = client, model, model_deep

    supports_web_search = False  # Gemini: product suggestions link to an Amazon search instead of a page

    def chat(self, system: str, tools: list[dict], prompt: str, on_tool: ToolCallback, deep: bool = False,
             history: list[dict] | None = None, web_search: bool = False,
             image: tuple[bytes, str] | None = None) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["input_schema"])
                for t in tools
            ])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [types.Content(role="user" if h["role"] == "user" else "model", parts=[types.Part(text=h["text"])])
                    for h in (history or [])]
        parts = [types.Part.from_bytes(data=image[0], mime_type=image[1])] if image else []
        contents.append(types.Content(role="user", parts=parts + [types.Part(text=prompt)]))
        for _ in range(MAX_STEPS):
            resp = self.client.models.generate_content(
                model=self.model_deep if deep else self.model, contents=contents, config=config,
            )
            if not resp.candidates or resp.candidates[0].content is None:
                return "The AI returned no answer (possibly blocked by a safety filter)."
            reply = resp.candidates[0].content
            contents.append(reply)  # keep it whole: Gemini needs its thought signatures back
            parts = reply.parts or []
            calls = [p.function_call for p in parts if p.function_call]
            if not calls:
                return "\n".join(p.text for p in parts if p.text and not p.thought).strip()
            responses = []
            for fc in calls:
                out, err = on_tool(fc.name, dict(fc.args or {}))
                try:
                    payload = json.loads(out)
                except (TypeError, ValueError):
                    payload = out
                part = types.Part.from_function_response(
                    name=fc.name, response={"error": payload} if err else {"result": payload})
                if fc.id:
                    part.function_response.id = fc.id
                responses.append(part)
            contents.append(types.Content(role="user", parts=responses))
        return STOPPED


PROVIDERS = {"claude": ClaudeProvider, "gemini": GeminiProvider}


def make_provider(name: str, settings) -> ClaudeProvider | GeminiProvider:
    if name == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_model_deep)
    if name == "claude":
        return ClaudeProvider(settings.anthropic_api_key, settings.model, settings.model_deep)
    raise ValueError(f"unknown provider {name!r}; use 'claude' or 'gemini'")
