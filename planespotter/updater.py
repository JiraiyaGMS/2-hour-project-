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


def _members(data: bytes) -> list[tuple[PurePosixPath, bytes, bool]]:
    """(path inside planespotter's folder, contents, executable) for every ordinary file in the download that
    may be written: never your settings, links, or anything pointing outside."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        members = tar.getmembers()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise UpdateError(f"The download is incomplete ({exc}). Try again.") from exc
    out = []
    for m in members:
        parts = PurePosixPath(m.name).parts[1:]  # without the download's own top folder
        if not m.isfile() or not parts or ".." in parts or PurePosixPath(m.name).is_absolute() or parts[0] in NEVER:
            continue
        out.append((PurePosixPath(*parts), tar.extractfile(m).read(), bool(m.mode & 0o100)))
    if not all(need in {str(p) for p, _, _ in out} for need in MUST_HAVE):
        raise UpdateError("That download isn't planespotter. Try again in a few minutes.")
    return out


def unpack(data: bytes, target: Path, backup: Path | None = None) -> tuple[int, int]:
    """Put the download's files in ``target``. Returns (files that changed, files in the download). Only
    ordinary files, only inside ``target``, never your settings. With ``backup``, each file it replaces is
    copied there first (and a list of the ones it adds), so ``restore`` can put things back."""
    files = _members(data)
    target = target.resolve()
    changed = 0
    added: list[str] = []
    for rel, new, executable in files:
        dest = (target / Path(*rel.parts)).resolve()
        if target not in dest.parents:
            continue
        try:
            if dest.is_file() and dest.read_bytes() == new:
                continue
            if backup is not None:
                if dest.is_file():
                    keep = backup / Path(*rel.parts)
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    keep.write_bytes(dest.read_bytes())
                else:
                    added.append(str(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".updating")
            tmp.write_bytes(new)
            os.chmod(tmp, 0o755 if executable else 0o644)
            os.replace(tmp, dest)  # the whole file or none of it
        except OSError as exc:
            raise UpdateError(f"Couldn't write {dest} ({exc.strerror or exc}).") from exc
        changed += 1
    if backup is not None:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / ".added").write_text("\n".join(added), encoding="utf-8")
    return changed, len(files)


def restore(backup: Path, target: Path) -> None:
    """Put back what ``unpack(..., backup=...)`` replaced, and take away what it added."""
    target = target.resolve()
    for kept in backup.rglob("*"):
        if kept.is_file() and kept.name != ".added":
            dest = (target / kept.relative_to(backup)).resolve()
            if target in dest.parents:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(kept, dest) if kept.stat().st_dev == dest.parent.stat().st_dev else dest.write_bytes(kept.read_bytes())
    added = backup / ".added"
    for rel in (added.read_text(encoding="utf-8").splitlines() if added.exists() else []):
        dest = (target / rel).resolve()
        if rel and target in dest.parents and dest.is_file():
            dest.unlink()


SELF_CHECK = ("import importlib, pkgutil, sys, planespotter\n"
              "for m in pkgutil.iter_modules(planespotter.__path__):\n"
              "    importlib.import_module('planespotter.' + m.name)\n"
              "from planespotter.config import Config\n"
              "Config.load(sys.argv[1])\n")


def healthy(port: int, seconds: float = 120, opener=urllib.request.urlopen, sleep=None) -> bool:
    """Does planespotter answer on this computer again (its sign-in page, or its map)?"""
    import time as _time

    sleep = sleep or _time.sleep
    end = _time.time() + seconds
    while True:
        for path in ("/login", "/api/config"):
            try:
                with opener(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                    if resp.status == 200:
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code in (303, 401):  # (the sign-in page answering)
                    return True
            except (urllib.error.URLError, OSError):
                pass
        if _time.time() >= end:
            return False
        sleep(5)


def nightly(env_path: Path, project: Path, fetch=download, run=subprocess.run, check=None, log=print) -> int:
    """By itself, once a night: the newest version, but only if it works. The new files are checked before
    planespotter switches to them, and the site after; if either fails, yesterday's version comes back.
    Returns 0 (up to date, or updated) or 1 (kept yesterday's)."""
    import shutil
    import time as _time

    from .config import Config

    stamp = _time.strftime("%Y-%m-%d %H:%M")
    try:
        data = fetch()
    except UpdateError as exc:
        log(f"{stamp} nightly update: couldn't download, nothing changed ({exc})")
        return 1
    backup = project / ".update-backup"
    shutil.rmtree(backup, ignore_errors=True)
    try:
        changed, total = unpack(data, project, backup=backup)
    except UpdateError as exc:
        restore(backup, project)
        log(f"{stamp} nightly update: {exc} Kept yesterday's version.")
        return 1
    if not changed:
        shutil.rmtree(backup, ignore_errors=True)
        log(f"{stamp} nightly update: already up to date")
        return 0
    me = [sys.executable, "-m", "planespotter", "--env", str(env_path)]
    test = run([sys.executable, "-c", SELF_CHECK, str(env_path)], cwd=str(project), capture_output=True, text=True)
    if test.returncode != 0:
        restore(backup, project)
        log(f"{stamp} nightly update: the new version didn't load, kept yesterday's "
            f"({(test.stderr or '').strip().splitlines()[-1:] or ['?']})")
        return 1
    port = Config.load(env_path).map_port
    run([*me, "autostart"], cwd=str(project), capture_output=True, text=True)  # restart with the new version
    if not (check or healthy)(port):
        restore(backup, project)
        run([*me, "autostart"], cwd=str(project), capture_output=True, text=True)
        log(f"{stamp} nightly update: the new version didn't start, went back to yesterday's")
        return 1
    log(f"{stamp} nightly update: updated ({changed} of {total} files) and running")
    return 0


def cmd_update(env_path: Path, args, project_dir: Path | None = None, fetch=download, run=subprocess.run) -> int:
    from . import autostart
    from .config import PROJECT_DIR

    project = project_dir or PROJECT_DIR
    if getattr(args, "nightly", False):  # by itself, from the nightly timer: safely, and say so in update.log
        def log(line: str) -> None:
            print(line)
            try:
                with open(project / "update.log", "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        return nightly(env_path, project, fetch, run, log=log)
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
    if getattr(args, "nightly", False):
        return 0
    accounts_on = (project / "accounts.db").exists() or Path(env_path).with_name("accounts.db").exists()
    if env_path.exists() and sys.platform == "darwin" and not accounts_on:
        print("\nPutting the newest script on your iPhone…")
        done = run([*me, "phone", "--install"], cwd=str(project))
        if getattr(done, "returncode", 0) != 0:
            print("  (The copy-and-paste way works everywhere:  python3 -m planespotter phone --copy widget)")
    return 0
