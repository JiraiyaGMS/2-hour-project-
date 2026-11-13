"""Run planespotter by itself: from login (or boot), restarted if it ever stops, and only ever once.

`python3 -m planespotter autostart` sets this up with the computer's own tools:

* on a Mac, a LaunchAgent: it starts when you log in, again within a minute if it stops, and keeps
  the Mac from going to sleep while it's plugged in (so the plane alerts keep coming);
* on Linux (a cloud server, a Raspberry Pi), a systemd service: it starts when the computer boots,
  even with nobody logged in, and again within a minute if it stops.

Its messages go to planespotter.log, which trims itself.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.planespotter.watch"
SERVICE = "planespotter"
LOG_BYTES = 1_000_000   # planespotter.log is trimmed at about 1 MB (keeping 3 older copies)


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(python: str, env_path: Path, project_dir: Path, log_path: Path) -> bytes:
    """The LaunchAgent: run `watch` from login, again if it stops, and keep the Mac awake meanwhile."""
    return plistlib.dumps({
        "Label": LABEL,
        # caffeinate -i: don't idle-sleep; -s: don't sleep at all while plugged in.
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", "-s", python, "-m", "planespotter",
                             "--env", str(env_path), "--log-file", str(log_path), "watch"],
        "WorkingDirectory": str(project_dir),
        "RunAtLoad": True,
        "KeepAlive": True,          # stopped (or crashed)? start again
        "ThrottleInterval": 60,     # ...but at most once a minute
        "StandardOutPath": str(project_dir / "planespotter-startup.log"),
        "StandardErrorPath": str(project_dir / "planespotter-startup.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1",
                                 "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    })


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def supported() -> bool:
    return sys.platform == "darwin" or (sys.platform.startswith("linux") and shutil.which("systemctl") is not None)


def install(env_path: Path, project_dir: Path) -> tuple[bool, str]:
    return _mac_install(env_path, project_dir) if sys.platform == "darwin" else _linux_install(env_path, project_dir)


def uninstall() -> bool:
    return _mac_uninstall() if sys.platform == "darwin" else _linux_uninstall()


def status() -> dict:
    """{'installed': bool, 'running': bool, 'pid': int | None}."""
    return _mac_status() if sys.platform == "darwin" else _linux_status()


def _mac_install(env_path: Path, project_dir: Path) -> tuple[bool, str]:
    """Write the LaunchAgent and start it now. Returns (worked, what happened)."""
    log_path = project_dir / "planespotter.log"
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_plist(sys.executable, env_path.resolve(), project_dir.resolve(), log_path))
    (project_dir / "planespotter-startup.log").write_text("")  # a fresh start
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")  # an older copy, if any (fine if there isn't one)
    started = _launchctl("bootstrap", domain, str(path))
    if started.returncode != 0:
        started = _launchctl("load", "-w", str(path))  # older macOS
    if started.returncode != 0:
        return False, (started.stderr or started.stdout).strip()
    return True, str(log_path)


def _mac_uninstall() -> bool:
    """Stop it and stop it starting by itself. True if it was set up."""
    path = plist_path()
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if path.exists():
        _launchctl("unload", str(path))  # older macOS (harmless otherwise)
        path.unlink()
        return True
    return False


def _mac_status() -> dict:
    out = _launchctl("list", LABEL)
    pid = None
    for line in out.stdout.splitlines():
        if '"PID"' in line:
            digits = "".join(c for c in line if c.isdigit())
            pid = int(digits) if digits else None
    return {"installed": plist_path().exists(), "running": pid is not None, "pid": pid}


# --- Linux: a systemd service (cloud servers, a Raspberry Pi) -------------------------

def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE}.service"


def _quote(arg: str) -> str:
    return f'"{arg}"' if any(c.isspace() for c in arg) else arg


def build_unit(python: str, env_path: Path, project_dir: Path, log_path: Path) -> str:
    """The systemd service: run `watch` from boot, and again within a minute if it ever stops."""
    command = " ".join(_quote(str(a)) for a in (python, "-m", "planespotter", "--env", env_path,
                                                 "--log-file", log_path, "watch"))
    return (
        "[Unit]\n"
        "Description=planespotter: plane alerts for your house\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"WorkingDirectory={_quote(str(project_dir))}\n"
        f"ExecStart={command}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=always\n"
        "RestartSec=60\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], capture_output=True, text=True)


def _linux_install(env_path: Path, project_dir: Path, run=subprocess.run) -> tuple[bool, str]:
    log_path = project_dir / "planespotter.log"
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(sys.executable, env_path.resolve(), project_dir.resolve(), log_path))
    for step in (("daemon-reload",), ("enable", SERVICE), ("restart", SERVICE)):
        done = _systemctl(*step, run=run)
        if done.returncode != 0:
            return False, (done.stderr or done.stdout).strip()
    # Keep running after you log out (and start at boot without anyone logging in).
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    linger = run(["loginctl", "enable-linger", user], capture_output=True, text=True)
    if linger.returncode != 0:  # over SSH it can need admin rights (cloud servers' ubuntu user has them)
        linger = run(["sudo", "-n", "loginctl", "enable-linger", user], capture_output=True, text=True)
    if linger.returncode != 0:
        return True, f"{log_path}\n  ⚠ To keep it running after you log out, run:  sudo loginctl enable-linger {user}"
    return True, str(log_path)


def _linux_uninstall(run=subprocess.run) -> bool:
    _systemctl("disable", "--now", SERVICE, run=run)
    path = unit_path()
    if path.exists():
        path.unlink()
        _systemctl("daemon-reload", run=run)
        return True
    return False


def _linux_status(run=subprocess.run) -> dict:
    out = _systemctl("show", SERVICE, "-p", "ActiveState", "-p", "MainPID", run=run).stdout
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    pid = int(values.get("MainPID") or 0) or None
    return {"installed": unit_path().exists(), "running": values.get("ActiveState") == "active" and pid is not None,
            "pid": pid}


# --- only once, and a log that trims itself ---------------------------------------

def single_instance(db_path: Path):
    """Hold a lock while planespotter runs, so a second copy can tell. None if one is already running."""
    lock_path = Path(db_path).with_name(Path(db_path).name + ".lock")
    try:
        import fcntl
    except ImportError:  # Windows: no lock (and no autostart)
        return open(os.devnull, "w")
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle  # keep it: the lock lasts as long as this file stays open


class _LogWriter:
    """Sends print() output to the log too, so nothing grows without limit."""

    def __init__(self, logger: logging.Logger):
        self.logger, self.buffer = logger, ""

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.logger.info(line.rstrip())
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def log_to_file(path: Path, level: int) -> None:
    """Log (and print) to a file that's trimmed at about 1 MB, instead of the Terminal."""
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    sys.stdout = sys.stderr = _LogWriter(logging.getLogger("planespotter.output"))
