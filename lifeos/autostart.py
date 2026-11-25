"""Start Life OS when the computer starts (Settings > This computer, or the menu bar icon)."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

LABEL = "uk.lifeos.app"


def command() -> list[str]:
    """How to start this copy of Life OS, without opening the browser."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--no-browser"]
    boot = Path(__file__).resolve().parent.parent / "lifeos_boot.py"
    return [sys.executable, str(boot), "--no-browser"]


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_unit() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / "lifeos.service"


def supported() -> bool:
    return sys.platform in ("darwin", "win32") or sys.platform.startswith("linux")


def enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, "Life OS")
                return True
        except OSError:
            return False
    return _linux_unit().exists()


def set_enabled(on: bool) -> bool:
    cmd = command()
    if sys.platform == "darwin":
        plist = _mac_plist()
        if on:
            import plistlib
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_bytes(plistlib.dumps({
                "Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},   # restarted if it stops unexpectedly, not after Quit
                "EnvironmentVariables": {"LIFEOS_HOME": os.environ.get("LIFEOS_HOME", "")},
                "StandardOutPath": str(Path(os.environ.get("LIFEOS_HOME", "/tmp")) / "data" / "lifeos-console.log"),
                "StandardErrorPath": str(Path(os.environ.get("LIFEOS_HOME", "/tmp")) / "data" / "lifeos-console.log")}))
        elif plist.exists():
            plist.unlink()
        return enabled()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, "Life OS", 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, "Life OS")
                except OSError:
                    pass
        return enabled()
    unit = _linux_unit()
    if on:
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_unit_text(cmd, user=None))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "lifeos.service"], check=False, capture_output=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "lifeos.service"], check=False, capture_output=True)
        if unit.exists():
            unit.unlink()
    return enabled()


def _unit_text(cmd: list[str], user: str | None) -> str:
    home = os.environ.get("LIFEOS_HOME", "")
    return "\n".join([
        "[Unit]", "Description=Life OS household assistant", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", *([f"User={user}"] if user else []), f"Environment=LIFEOS_HOME={home}", "Environment=TZ=Europe/London",
        f"ExecStart={shlex.join([cmd[0], *[a for a in cmd[1:] if a != '--no-browser'], '--headless'])}",
        "Restart=on-failure", "RestartSec=5", "SuccessExitStatus=0", "RestartForceExitStatus=75", "", "[Install]",
        "WantedBy=" + ("multi-user.target" if user else "default.target"), ""])


def install_linux_service() -> int:
    """`lifeos install-service`: a system service on a Linux box or Raspberry Pi, running as you."""
    if not sys.platform.startswith("linux"):
        print("This is for Linux and the Raspberry Pi. On a Mac or Windows PC use Settings > This computer > "
              "Start Life OS when this computer starts.")
        return 1
    import getpass
    import tempfile
    user = getpass.getuser()
    with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as f:
        f.write(_unit_text(command(), user=user))
    steps = [["sudo", "cp", f.name, "/etc/systemd/system/lifeos.service"], ["sudo", "systemctl", "daemon-reload"],
             ["sudo", "systemctl", "enable", "--now", "lifeos.service"]]
    for s in steps:
        print("$", shlex.join(s))
        if subprocess.run(s).returncode != 0:
            print("That didn't work. You can run the lines above yourself.")
            return 1
    print("Life OS now starts whenever this machine starts. Logs: journalctl -u lifeos -f")
    return 0
