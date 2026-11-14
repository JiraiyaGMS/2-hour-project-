"""`python3 -m planespotter update`: get the newest planespotter, restart it, and update the phone's script.

One command instead of five, and it says plainly what's wrong when the download can't work (not online, a
Wi-Fi that wants you to sign in first, GitHub blocked). Your settings (.env), history and photos are never
touched: only planespotter's own files are replaced.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from .adsb import USER_AGENT

ARCHIVE = "https://github.com/JiraiyaGMS/2-hour-project-/archive/refs/heads/claude/exciting-cray-6oq525.tar.gz"
MUST_HAVE = ("planespotter/__main__.py", "planespotter/web/scriptable.js")
NEVER = {".env"}  # yours, whatever a download contains


class UpdateError(Exception):
    pass


def download(url: str = ARCHIVE, timeout: float = 60, opener=urllib.request.urlopen) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=timeout) as resp:
            data = resp.read(60_000_000)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub answered {exc.code}. Try again in a few minutes.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Couldn't reach GitHub: this computer isn't online, or this network blocks it "
                          f"({getattr(exc, 'reason', exc)}). Check Safari can open a website, then try again. "
                          "Your phone's Personal Hotspot works too.") from exc
    if data[:2] != b"\x1f\x8b":  # not a .tar.gz: something else answered
        raise UpdateError("Something else answered instead of GitHub: this Wi-Fi probably wants you to sign in first. "
                          "Open Safari, go to any website, accept its page, then try again.")
    return data


def unpack(data: bytes, target: Path) -> tuple[int, int]:
    """Put the download's files in ``target``. Returns (files that changed, files in the download).
    Only ordinary files, only inside ``target``, never your settings; nothing is written unless it's
    planespotter."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        members = tar.getmembers()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise UpdateError(f"The download is incomplete ({exc}). Try again.") from exc
    files: list[tuple[PurePosixPath, tarfile.TarInfo]] = []
    for m in members:
        parts = PurePosixPath(m.name).parts[1:]  # without the download's own top folder
        if not m.isfile() or not parts or ".." in parts or PurePosixPath(m.name).is_absolute():
            continue  # folders are made as needed; links and anything pointing outside are left out
        if parts[0] in NEVER or any(p.startswith("/") for p in parts):
            continue
        files.append((PurePosixPath(*parts), m))
    names = {str(p) for p, _ in files}
    if not all(need in names for need in MUST_HAVE):
        raise UpdateError("That download isn't planespotter. Try again in a few minutes.")
    changed = 0
    target = target.resolve()
    for rel, m in files:
        dest = (target / Path(*rel.parts)).resolve()
        if target not in dest.parents:
            continue
        new = tar.extractfile(m).read()
        try:
            if dest.is_file() and dest.read_bytes() == new:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".updating")
            tmp.write_bytes(new)
            os.chmod(tmp, 0o755 if m.mode & 0o100 else 0o644)
            os.replace(tmp, dest)  # the whole file or none of it
        except OSError as exc:
            raise UpdateError(f"Couldn't write {dest} ({exc.strerror or exc}).") from exc
        changed += 1
    return changed, len(files)


def cmd_update(env_path: Path, args, project_dir: Path | None = None, fetch=download, run=subprocess.run) -> int:
    from . import autostart
    from .config import PROJECT_DIR

    project = project_dir or PROJECT_DIR
    print("Getting the newest planespotter…")
    try:
        changed, total = unpack(fetch(), project)
    except UpdateError as exc:
        print(f"✘ {exc}")
        return 1
    if not changed:
        print(f"✔ Already up to date ({total} files checked).")
    else:
        print(f"✔ Updated ({changed} of {total} files changed). Your settings and history weren't touched.")
    me = [sys.executable, "-m", "planespotter", "--env", str(env_path)]
    try:
        installed = autostart.supported() and autostart.status().get("installed")
    except Exception:
        installed = False
    if changed and installed:
        print("\nRestarting planespotter with the new version…")
        run([*me, "autostart"], cwd=str(project))
    elif changed:
        print("\nIf planespotter is running in a Terminal window, stop it (Control-C) and start it again to use the "
              "new version. To have it run by itself:  python3 -m planespotter autostart")
    if env_path.exists():
        print("\nPutting the newest script on your iPhone…")
        done = run([*me, "phone", "--install"], cwd=str(project))
        if getattr(done, "returncode", 0) != 0:
            print("  (The copy-and-paste way works everywhere:  python3 -m planespotter phone --copy widget)")
    return 0
