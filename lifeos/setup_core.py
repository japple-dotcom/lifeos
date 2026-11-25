"""Writing the household's settings: shared by the setup page, the Settings screen and `lifeos setup`."""
from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path

import yaml

from . import paths

WORDS = ["amber", "apple", "atlas", "beacon", "birch", "breeze", "cedar", "cliff", "clover", "comet", "coral", "crane",
         "delta", "ember", "fable", "fern", "field", "flint", "forest", "frost", "glade", "grove", "harbor", "hazel",
         "heron", "island", "ivy", "juniper", "kestrel", "lagoon", "lantern", "linden", "maple", "meadow", "meteor",
         "mint", "moss", "nectar", "oak", "orbit", "orchard", "otter", "pebble", "pine", "plume", "quartz", "raven",
         "reef", "ridge", "river", "robin", "saffron", "sage", "shore", "sparrow", "spruce", "summit", "thistle",
         "thunder", "tide", "timber", "topaz", "valley", "willow", "wren"]
PLACEHOLDER = re.compile(r"^(|change-me.*|.*\.\.\.)$")
# Only these can be changed from the app; anything else in .env is left exactly as it is.
EDITABLE = {"LIFEOS_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LIFEOS_PUBLIC_URL", "HA_URL", "HA_TOKEN",
            "DVLA_API_KEY", "OCTOPUS_TARIFF"}


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or paths.env_file()
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def is_set(value: str | None) -> bool:
    return bool(value) and not PLACEHOLDER.match(value or "")


def needs_setup() -> bool:
    env = {**read_env(), **{k: v for k, v in os.environ.items() if k.startswith("LIFEOS_")}}
    return not (is_set(env.get("LIFEOS_API_KEY")) and len(env.get("LIFEOS_API_KEY", "")) >= 12
                and is_set(env.get("LIFEOS_SIGNING_SECRET")) and len(env.get("LIFEOS_SIGNING_SECRET", "")) >= 24)


def household_key() -> str:
    """A memorable key, easy to type on a phone once: e.g. 'maple-orbit-heron-tide-42'."""
    return "-".join(secrets.SystemRandom().sample(WORDS, 4)) + f"-{secrets.randbelow(90) + 10}"


def write_env(values: dict[str, str], path: Path | None = None) -> None:
    """Merges values into .env, keeping comments and anything not being changed."""
    path = path or paths.env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    example = paths.CODE_ROOT / ".env.example"
    source = path if path.exists() else (example if example.exists() else None)
    template = source.read_text().splitlines() if source else []
    seen, lines = set(), []
    for line in template:
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m and m.group(1) in values:
            lines.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            lines.append(line)
    extra = [f"{k}={v}" for k, v in values.items() if k not in seen]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines + extra) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def set_household(adult_names: list[str], kids: list[str], area: str | None = None, settings: Path | None = None) -> None:
    settings = settings or paths.settings_file()
    paths.ensure_home()
    text = settings.read_text()
    adult_names = [a.strip() for a in adult_names if a.strip()][:4] or ["Me"]
    ids = ["me", "wife"][:len(adult_names)] if len(adult_names) <= 2 else [f"adult{i + 1}" for i in range(len(adult_names))]
    names = ", ".join(f"{i}: {_yaml_word(n)}" for i, n in zip(ids, adult_names, strict=False))
    text = re.sub(r"(?m)^  adults: \[.*\]$", f"  adults: [{', '.join(ids)}]", text)
    text = re.sub(r"(?m)^  names: \{.*\}$", f"  names: {{{names}}}", text)
    text = re.sub(r"(?m)^  kids: \[.*$", f"  kids: [{', '.join(_yaml_word(k) for k in kids if k.strip())}]", text)
    if area is not None:
        text = re.sub(r'(?m)^  area: .*$', f"  area: {_yaml_word(area) if area.strip() else chr(34) * 2}", text)
    yaml.safe_load(text)  # never write a settings file that doesn't read back
    settings.write_text(text)


def _yaml_word(s: str) -> str:
    s = re.sub(r"[\r\n]", " ", s).strip()[:60]
    return s if re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]*", s) else '"' + s.replace("\\", "").replace('"', "'") + '"'


def household(settings: Path | None = None) -> dict:
    settings = settings or (paths.settings_file() if paths.settings_file().exists() else paths.default_settings_file())
    return (yaml.safe_load(settings.read_text()) or {}).get("household", {}) or {}


def first_setup(adults: list[str], kids: list[str], area: str, provider: str, anthropic_key: str, gemini_key: str,
                public_url: str, ha_url: str = "", ha_token: str = "") -> dict:
    """Everything the setup page asks for. Returns the household key to show once."""
    paths.ensure_home()
    set_household(adults, kids, area)
    env = read_env()
    key = env.get("LIFEOS_API_KEY", "")
    if not is_set(key) or len(key) < 12:
        key = household_key()
    values = {"LIFEOS_API_KEY": key,
              "LIFEOS_SIGNING_SECRET": env.get("LIFEOS_SIGNING_SECRET") if is_set(env.get("LIFEOS_SIGNING_SECRET"))
              and len(env.get("LIFEOS_SIGNING_SECRET", "")) >= 24 else secrets.token_urlsafe(32),
              "LIFEOS_PROVIDER": "gemini" if provider.lower().startswith("g") else "claude",
              "LIFEOS_PUBLIC_URL": (public_url or "http://localhost:8800").strip().rstrip("/")}
    if anthropic_key.strip():
        values["ANTHROPIC_API_KEY"] = anthropic_key.strip()
    if gemini_key.strip():
        values["GEMINI_API_KEY"] = gemini_key.strip()
    if ha_url.strip():
        values["HA_URL"] = ha_url.strip().rstrip("/")
    if ha_token.strip():
        values["HA_TOKEN"] = ha_token.strip()
    if is_set(env.get("NTFY_TOPIC")) and env["NTFY_TOPIC"].startswith("lifeos-change-me"):
        values["NTFY_TOPIC"] = ""
    write_env(values)
    return {"household_key": key}


def update_env(changes: dict[str, str]) -> list[str]:
    """From Settings: only EDITABLE keys, blank means 'leave as it is'. Returns the names changed (never values)."""
    values = {k: v.strip() for k, v in changes.items() if k in EDITABLE and v is not None and v.strip()}
    if "LIFEOS_PUBLIC_URL" in values:
        values["LIFEOS_PUBLIC_URL"] = values["LIFEOS_PUBLIC_URL"].rstrip("/")
        if not values["LIFEOS_PUBLIC_URL"].startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("The address must start with https:// (or be http://localhost for this computer only).")
    if "LIFEOS_PROVIDER" in values and values["LIFEOS_PROVIDER"] not in ("claude", "gemini"):
        raise ValueError("Choose Claude or Gemini.")
    if values:
        write_env(values)
    return sorted(values)


# ---------- bringing an older install across ----------
def _looks_like_install(folder: Path) -> bool:
    env = read_env(folder / ".env")
    return is_set(env.get("LIFEOS_API_KEY")) and ((folder / "lifeos").is_dir() or (folder / "data").is_dir())


def find_old_installs(start: Path | None = None) -> list[dict]:
    """Life OS folders from before the app (e.g. ~/Documents/lifeos), so their settings can be brought across."""
    base = start or Path.home()
    here = paths.home().resolve()
    seen, found = set(), []
    for parent in (base, base / "Documents"):  # (a Mac asks before an app looks in Desktop or Downloads)
        try:
            candidates = [p for p in parent.iterdir() if p.is_dir() and p.name.lower().startswith("lifeos")]
        except OSError:
            continue
        for c in candidates:
            for folder in (c, c / "lifeos"):
                r = folder.resolve()
                if r in seen or r == here or not (folder / ".env").exists() or not _looks_like_install(folder):
                    continue
                seen.add(r)
                hh = household(folder / "config" / "settings.yaml") if (folder / "config" / "settings.yaml").exists() else {}
                names = [(hh.get("names") or {}).get(a, a) for a in hh.get("adults") or []]
                google = sorted(p.stem.replace("google_token_", "") for p in (folder / "secrets").glob("google_token_*.json"))
                found.append({"path": str(folder), "names": names, "google": google,
                              "history": (folder / "data" / "lifeos.db").exists()})
    return found


def bring_across(folder: str) -> dict:
    src = Path(folder).expanduser()
    if not any(Path(f["path"]) == src for f in find_old_installs()):
        raise ValueError("That isn't a Life OS folder found on this computer.")
    dst = paths.ensure_home()
    shutil.copy2(src / ".env", dst / ".env")
    for sub in ("secrets", "data"):
        if (src / sub).is_dir():
            shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)
    if (src / "config" / "settings.yaml").exists():
        shutil.copy2(src / "config" / "settings.yaml", dst / "config" / "settings.yaml")
    return {"ok": True, "names": [n for f in find_old_installs() if Path(f["path"]) == src for n in f["names"]]}


# ---------- the same questions in a terminal (servers without a screen) ----------
def _ask(question: str, current: str = "", secret: bool = False, default: str = "") -> str:
    import getpass
    import sys
    shown = "(already set, Enter to keep)" if current and secret else (f"[{current or default}]" if (current or default) else "")
    prompt = f"{question} {shown}: ".replace("  ", " ")
    value = (getpass.getpass(prompt) if secret and sys.stdin.isatty() else input(prompt)).strip()
    return value or current or default


def cli() -> None:
    print(f"\nLife OS setup. Settings are kept in {paths.home()}\nPress Enter to accept the value in [brackets].\n")
    paths.ensure_home()
    env = read_env()
    hh = household()
    print("1) Your household")
    adults = _ask("First names of the adults, comma separated (you first)",
                  ", ".join((hh.get("names") or {}).get(i, i) for i in hh.get("adults") or []), default="Me")
    kids = _ask("Children's first names, comma separated (blank if none)", ", ".join(hh.get("kids") or []))
    area = _ask("Town and postcode area, for finding local tradespeople (e.g. Leeds LS6)", hh.get("area") or "")
    print("\n2) The assistant (one at a time; you can switch later in the app)")
    provider = _ask("Claude or Gemini?", env.get("LIFEOS_PROVIDER", ""), default="claude").lower()
    akey = gkey = ""
    if provider.startswith("g"):
        gkey = _ask("Gemini API key (aistudio.google.com/apikey)", "", secret=True)
    else:
        akey = _ask("Claude API key (console.anthropic.com)", "", secret=True)
    print("\n3) How your phones reach Life OS")
    print("   Run 'tailscale serve --bg 8800' on this machine and paste the https address it shows.")
    url = _ask("Life OS address", env.get("LIFEOS_PUBLIC_URL", "") if "your-tailnet" not in env.get("LIFEOS_PUBLIC_URL", "")
               else "", default="http://localhost:8800")
    print("\n4) Home Assistant (blank to add later)")
    ha_url = _ask("Home Assistant address", env.get("HA_URL", ""))
    ha_token = _ask("Home Assistant long-lived token", "", secret=True)
    r = first_setup([a for a in adults.split(",")], [k.strip() for k in kids.split(",") if k.strip()], area, provider,
                    akey, gkey, url, ha_url, ha_token)
    print(f"\nDone. Your household key is:  {r['household_key']}")
    print("Type it once on each phone to sign in. Keep it somewhere safe.\n")
