"""Loads secrets from the environment and behaviour from config/settings.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import paths

ROOT = paths.CODE_ROOT


@dataclass
class Settings:
    provider: str = "claude"          # the one active AI: "claude" or "gemini"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    model_deep: str = "claude-opus-5-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_model_deep: str = "gemini-3.8-flash"
    ha_url: str = ""
    ha_token: str = ""
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    public_url: str = "http://localhost:8800"
    api_key: str = ""
    signing_secret: str = ""
    google_client_secret_file: str = "secrets/client_secret.json"
    google_token_file: str = "secrets/google_token.json"
    family_calendar_id: str = "primary"
    db_path: str = "data/lifeos.db"
    tz: str = "Europe/London"
    max_runs_per_day: int = 150
    push_contact: str = "mailto:lifeos@example.com"
    demo: bool = False  # scripts/demo.py: sample data, no real accounts
    behaviour: dict = field(default_factory=dict)

    @property
    def policy(self) -> dict:
        return self.behaviour.get("policy", {})

    @property
    def tags(self) -> dict:
        return self.behaviour.get("tags", {}) or {}

    @property
    def loops(self) -> dict:
        return self.behaviour.get("loops", {}) or {}

    @property
    def household(self) -> dict:
        return self.behaviour.get("household", {}) or {}

    @property
    def quick_actions(self) -> list[dict]:
        return self.behaviour.get("quick_actions", []) or []

    @property
    def home_status(self) -> list[str]:
        return self.behaviour.get("home_status", []) or []

    @property
    def task_lists(self) -> list[str]:
        return self.behaviour.get("task_lists", []) or []


PLACEHOLDERS = {"", "change-me", "change-me-too"}


def setup_problems(s: Settings) -> list[str]:
    """Plain-English list of things that stop Life OS working properly. Shown at startup and in the app."""
    problems = []
    if s.demo:
        return problems
    if s.api_key in PLACEHOLDERS or len(s.api_key) < 12:
        problems.append("LIFEOS_API_KEY (the household key) is missing or too short; use at least 12 characters.")
    if s.signing_secret in PLACEHOLDERS or len(s.signing_secret) < 24:
        problems.append("LIFEOS_SIGNING_SECRET is missing or too short; use 24+ random characters.")
    if s.provider == "claude" and not s.anthropic_api_key:
        problems.append("The assistant is set to Claude but ANTHROPIC_API_KEY is empty.")
    if s.provider == "gemini" and not s.gemini_api_key:
        problems.append("The assistant is set to Gemini but GEMINI_API_KEY is empty.")
    local = s.public_url.startswith(("http://localhost", "http://127.0.0.1"))
    if local:
        problems.append("Running on this computer only (http://localhost): Google sign-in works here, but phones "
                        "can't use Life OS until it has an https address. See README step 2 (Tailscale).")
    elif not s.public_url.startswith("https://"):
        problems.append("LIFEOS_PUBLIC_URL is not https; the phone app cannot be installed and Google sign-in "
                        "will not work. See README step 2 (Tailscale).")
    return problems


def _in_home(value: str) -> str:
    """Relative paths (secrets/..., data/...) are inside the Life OS home folder, wherever it runs from."""
    p = Path(value).expanduser()
    return str(p if p.is_absolute() or value == ":memory:" else paths.home() / p)


def load_settings(settings_file: str | Path | None = None) -> Settings:
    load_dotenv(paths.env_file())
    path = Path(settings_file or os.getenv("LIFEOS_SETTINGS") or paths.settings_file())
    if not path.exists():
        path = paths.default_settings_file()
    behaviour = yaml.safe_load(path.read_text()) if path.exists() else {}
    env = os.getenv
    return Settings(
        provider=env("LIFEOS_PROVIDER", "claude").strip().lower(),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        model=env("LIFEOS_MODEL", "claude-sonnet-5-5"),
        model_deep=env("LIFEOS_MODEL_DEEP", "claude-opus-5-5"),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        gemini_model=env("GEMINI_MODEL", "gemini-3.8-flash"),
        gemini_model_deep=env("GEMINI_MODEL_DEEP", env("GEMINI_MODEL", "gemini-3.8-flash")),
        ha_url=env("HA_URL", "").rstrip("/"),
        ha_token=env("HA_TOKEN", ""),
        ntfy_url=env("NTFY_URL", "https://ntfy.sh").rstrip("/"),
        ntfy_topic=env("NTFY_TOPIC", ""),
        ntfy_token=env("NTFY_TOKEN", ""),
        public_url=env("LIFEOS_PUBLIC_URL", "http://localhost:8800").rstrip("/"),
        api_key=env("LIFEOS_API_KEY", ""),
        signing_secret=env("LIFEOS_SIGNING_SECRET", ""),
        google_client_secret_file=_in_home(env("GOOGLE_CLIENT_SECRET_FILE", "secrets/client_secret.json")),
        google_token_file=_in_home(env("GOOGLE_TOKEN_FILE", "secrets/google_token.json")),
        family_calendar_id=env("GOOGLE_FAMILY_CALENDAR_ID", "primary"),
        db_path=_in_home(env("LIFEOS_DB", "data/lifeos.db")),
        tz=env("LIFEOS_TZ", "Europe/London"),
        max_runs_per_day=int(env("LIFEOS_MAX_RUNS_PER_DAY", "150") or 0),
        push_contact=env("LIFEOS_PUSH_CONTACT", "mailto:lifeos@example.com"),
        behaviour=behaviour or {},
    )
