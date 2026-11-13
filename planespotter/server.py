"""Move planespotter from your Mac to an always-on server (a free cloud computer), and back.

    python3 -m planespotter server ubuntu@203.0.113.5   # move it there (once)
    python3 -m planespotter server status               # how it's doing
    python3 -m planespotter server update               # the newest planespotter, there
    python3 -m planespotter server stop                 # stop it there, and bring its history back

Moving it: installs planespotter on the server (it only needs Python 3, which Ubuntu has), copies
your settings (with your time zone, since servers run on UTC) and your history, starts it there so it
runs by itself from boot, and stops it on this Mac, so every alert comes once. Everything goes over
SSH, with the key you gave the server when you made it.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

DOWNLOAD_URL = "https://github.com/JiraiyaGMS/2-hour-project-/archive/refs/heads/claude/exciting-cray-6oq525.tar.gz"
REMOTE_DIR = "~/planespotter"
INSTALL = f"mkdir -p {REMOTE_DIR} && curl -fsSL {DOWNLOAD_URL} | tar -xz -C {REMOTE_DIR} --strip-components=1"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20"]
# Settings that only make sense on this computer.
LOCAL_ONLY = {"DB_PATH", "OUTBOX_DIR", "SERVER", "MAP_LAN", "MAP_HTTPS", "RECEIVER_URL"}


class ServerError(Exception):
    pass


def check_target(target: str) -> str:
    """'ubuntu@203.0.113.5' (a user and the server's address)."""
    target = target.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.:_-]+", target):
        raise ServerError("Give the server as user@address, like:  ubuntu@203.0.113.5")
    return target


class Remote:
    """Runs commands on the server over SSH (``run`` is swapped out in the tests)."""

    def __init__(self, target: str, run=subprocess.run):
        self.target, self.run = check_target(target), run

    def ssh(self, command: str, timeout: float = 300) -> subprocess.CompletedProcess:
        return self.run(["ssh", *SSH_OPTIONS, self.target, command], capture_output=True, text=True,
                        timeout=timeout)

    def copy_to(self, local: Path, remote: str) -> subprocess.CompletedProcess:
        return self.run(["scp", *SSH_OPTIONS, str(local), f"{self.target}:{remote}"], capture_output=True,
                        text=True, timeout=600)

    def copy_from(self, remote: str, local: Path) -> subprocess.CompletedProcess:
        return self.run(["scp", *SSH_OPTIONS, f"{self.target}:{remote}", str(local)], capture_output=True,
                        text=True, timeout=600)

    def must(self, done: subprocess.CompletedProcess, what: str) -> str:
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
            raise ServerError(f"{what} didn't work: {detail[0]}")
        return done.stdout


def local_timezone() -> str:
    """This computer's time zone name, like 'America/Los_Angeles' ('' if it can't tell)."""
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"].lstrip(":")
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def server_env(env_text: str, timezone: str) -> str:
    """Your settings, for the server: your time zone written in, and this computer's own paths left out."""
    lines, has_tz = [], False
    for line in env_text.splitlines():
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and key in LOCAL_ONLY:
            continue
        if not line.lstrip().startswith("#") and key == "TIMEZONE" and line.split("=", 1)[1].strip():
            has_tz = True
        lines.append(line)
    if not has_tz and timezone:
        lines.append(f"TIMEZONE={timezone}")
    lines += ["", "# On the server: the map is only for the server itself, not open to the internet",
              "MAP_LAN=false", "MAP_HTTPS=false"]
    return "\n".join(lines) + "\n"


def snapshot(db_path: Path, out: Path) -> None:
    """A consistent copy of the database, even while something has it open."""
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(out))
    with target:
        source.backup(target)
    source.close()
    target.close()


def move(remote: Remote, env_path: Path, db_path: Path, say=print) -> None:
    """Install planespotter on the server, copy your settings and history, and start it there."""
    say(f"Connecting to {remote.target}...")
    version = remote.must(remote.ssh("python3 -c 'import sys; print(sys.version_info[:2])'", timeout=60),
                          "Connecting to the server")
    nums = [int(x) for x in re.findall(r"\d+", version)[:2]]
    if nums < [3, 9]:
        raise ServerError(f"The server's Python is too old ({version.strip()}): planespotter needs 3.9 or newer.")
    say("  ✔ Connected. Downloading planespotter there...")
    remote.must(remote.ssh(INSTALL), "Downloading planespotter on the server")
    with tempfile.TemporaryDirectory() as tmp:
        env_copy = Path(tmp) / "settings.env"
        env_copy.write_text(server_env(env_path.read_text(encoding="utf-8"), local_timezone()), encoding="utf-8")
        say("  ✔ Copying your settings...")
        remote.must(remote.copy_to(env_copy, f"{REMOTE_DIR}/.env"), "Copying your settings")
        remote.must(remote.ssh(f"chmod 600 {REMOTE_DIR}/.env"), "Protecting your settings")
        has_history = remote.ssh(f"test -e {REMOTE_DIR}/planespotter.db").returncode == 0
        if Path(db_path).exists() and not has_history:
            say("  ✔ Copying your history (every plane so far)...")
            db_copy = Path(tmp) / "planespotter.db"
            snapshot(Path(db_path), db_copy)
            remote.must(remote.copy_to(db_copy, f"{REMOTE_DIR}/planespotter.db"), "Copying your history")
        elif has_history:
            say("  ✔ The server already has a history: keeping it.")
    say("  ✔ Starting it there, so it runs by itself from now on...")
    out = remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -m planespotter autostart"), "Starting planespotter there")
    for line in out.splitlines():
        if "⚠" in line:
            say(line)


def status(remote: Remote) -> str:
    return remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -m planespotter autostart status", timeout=60),
                       "Checking on the server")


def update(remote: Remote) -> str:
    remote.must(remote.ssh(INSTALL), "Downloading the newest planespotter on the server")
    return remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -m planespotter autostart"), "Restarting it there")


def stop(remote: Remote, db_path: Path, say=print) -> None:
    """Stop it on the server and bring its history back to this computer."""
    remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -m planespotter autostart off"), "Stopping it on the server")
    say("  ✔ Stopped on the server. Bringing its history back...")
    remote.must(remote.ssh(
        f"cd {REMOTE_DIR} && python3 -c \"import sqlite3; s = sqlite3.connect('planespotter.db'); "
        f"t = sqlite3.connect('planespotter-copy.db'); s.backup(t); t.close()\""), "Copying the history on the server")
    with tempfile.TemporaryDirectory() as tmp:
        got = Path(tmp) / "planespotter.db"
        remote.must(remote.copy_from(f"{REMOTE_DIR}/planespotter-copy.db", got), "Bringing the history back")
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        shutil.copyfile(got, db_path)  # (the temporary folder may be on another disk)
    say("  ✔ Your history is back on this computer.")
