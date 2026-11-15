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
        copy_accounts(remote, Path(db_path).parent, Path(tmp), say)
    say("  ✔ Starting it there, so it runs by itself from now on...")
    out = remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -m planespotter autostart"), "Starting planespotter there")
    for line in out.splitlines():
        if "⚠" in line:
            say(line)


def copy_accounts(remote: Remote, folder: Path, tmp: Path, say=print) -> None:
    """With accounts on: the accounts, and each friend's settings and history, unless the server has them."""
    accounts_db = folder / "accounts.db"
    if not accounts_db.exists() or remote.ssh(f"test -e {REMOTE_DIR}/accounts.db").returncode == 0:
        return
    say("  ✔ Copying the accounts (and each friend's settings and history)...")
    snapshot(accounts_db, tmp / "accounts.db")
    remote.must(remote.copy_to(tmp / "accounts.db", f"{REMOTE_DIR}/accounts.db"), "Copying the accounts")
    for friend in sorted((folder / "accounts").glob("*/")) if (folder / "accounts").is_dir() else []:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,19}", friend.name):
            continue
        there = f"{REMOTE_DIR}/accounts/{friend.name}"
        remote.must(remote.ssh(f"mkdir -p {there} && chmod 700 {there}"), f"Making {friend.name}'s folder")
        if (friend / ".env").exists():
            remote.must(remote.copy_to(friend / ".env", f"{there}/.env"), f"Copying {friend.name}'s settings")
        if (friend / "planespotter.db").exists():
            snapshot(friend / "planespotter.db", tmp / f"{friend.name}.db")
            remote.must(remote.copy_to(tmp / f"{friend.name}.db", f"{there}/planespotter.db"), f"Copying {friend.name}'s history")


# Puts planespotter on your own web address, with the padlock (https). Caddy is a small free web server that
# gets and renews the certificate by itself; it passes everything to planespotter's sign-in page.
WEB_SETUP = r"""set -e
if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
fi
printf '%s {
  encode gzip
  reverse_proxy 127.0.0.1:{port}
}
' '{domain}' | sudo tee /etc/caddy/Caddyfile >/dev/null
# Let the web in (ports 80 and 443) through the server's own firewall, if it has one.
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q active; then sudo ufw allow 80/tcp && sudo ufw allow 443/tcp; fi
if sudo iptables -L INPUT -n 2>/dev/null | grep -q 'REJECT'; then
  for p in 80 443; do sudo iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT; done
  if command -v netfilter-persistent >/dev/null 2>&1; then sudo netfilter-persistent save >/dev/null 2>&1 || true; fi
fi
sudo systemctl enable --now caddy >/dev/null 2>&1 || true
sudo systemctl reload caddy || sudo systemctl restart caddy
"""


def check_domain(domain: str) -> str:
    d = domain.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not re.fullmatch(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", d):
        raise ServerError(f"{domain!r} doesn't look like a web address. Write it like  planebell.com")
    return d


def web(remote: Remote, domain: str, port: int, say=print) -> str:
    """Your own address with https, pointing at the server's planespotter. Returns the address."""
    domain = check_domain(domain)
    # On the internet, anyone can open it: only behind the sign-in page.
    if remote.ssh(f"test -e {REMOTE_DIR}/accounts.db").returncode != 0:
        raise ServerError("Turn accounts on first, so it asks everyone to sign in (otherwise anyone could see your "
                          "map and your home):  python3 -m planespotter accounts on  (then  server update)")
    say(f"Setting up https://{domain} on your server (a minute or two)...")
    remote.must(remote.ssh(WEB_SETUP.replace("{domain}", domain).replace("{port}", str(int(port))), timeout=600),
                "Setting up the web server (Caddy)")
    url = f"https://{domain}"
    remote.must(remote.ssh(f"cd {REMOTE_DIR} && python3 -c \"from pathlib import Path; from planespotter.wizard import "
                           f"set_env_value; set_env_value(Path('.env'), 'PUBLIC_URL', '{url}')\" && "
                           "python3 -m planespotter autostart"), "Telling planespotter its address")
    return url


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
