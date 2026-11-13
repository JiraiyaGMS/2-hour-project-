"""Jobs the home watcher runs after each check: Moon-crossing alerts, and the status for the widget and Siri."""

from __future__ import annotations

import logging

from .astro import transits
from .geo import compass
from .myflight import my_flights
from .outlook import best_time, best_time_text
from .predict import crossings, glide
from .relay import build_status
from .sky import sky_summary
from .space import when_text

log = logging.getLogger(__name__)


def transit_job(watcher):
    """A heads-up when a plane is about to cross in front of the Moon (or, if you ask, the Sun)."""
    told: dict[tuple[str, str], float] = {}

    def job(planes, now: float) -> None:
        cfg, notifier = watcher.cfg, watcher.notifier
        if not (cfg.transit_alerts and notifier and notifier.enabled) or cfg.in_quiet_hours(now):
            return
        bodies = ("Moon", "Sun") if cfg.transit_sun else ("Moon",)
        moved = [glide(ac, ac.seen_pos or 0) for ac in planes]
        for t in transits(cfg, moved, now, watcher.ground_ft, horizon=600, bodies=bodies, margin=0.15):
            key = (t.ac.hex, t.body)
            if t.at - now < 45 or now - told.get(key, -1e12) < 3600:
                continue  # too late to grab a camera, or already told
            told[key] = now
            at = cfg.local(t.at).strftime("%I:%M:%S %p").lstrip("0").lower()
            mins = round((t.at - now) / 60)
            icon = "🌕" if t.body == "Moon" else "☀️"
            what = "right across" if t.miss <= 0.26 else "just past"
            message = (f"At {at} ({'in about ' + str(mins) + ' min' if mins else 'in under a minute'}), look "
                       f"{compass(t.az)}, {round(t.el)}° up. It's over in a split second: start filming a few seconds "
                       f"early. (Predicted from its course, so it may turn.)")
            if t.body == "Sun":
                message += " Only ever look at or photograph the Sun through a proper solar filter."
            log.info("transit: %s %s the %s at %s", t.ac.label, what, t.body, at)
            notifier.send(f"{icon} {t.ac.label} may fly {what} the {t.body}", message,
                          click=notifier.link(plane=t.ac.hex), tags=("camera",))

    return job


def relay_job(watcher, relay, outlook):
    """Post the latest status (next planes, last one, the space station...) for the widget and Siri."""

    def job(planes, now: float) -> None:
        if not relay.enabled():
            return
        cfg = watcher.cfg
        moved = [glide(ac, ac.seen_pos or 0) for ac in planes]
        coming = crossings(cfg, moved, watcher.ground_ft, horizon=900)
        infos = {c.ac.hex: watcher.lookup.info(c.ac, now) for c in coming[:4]}
        last = watcher.store.recent_passes(0, limit=1)
        extras = {}
        if watcher.sky:
            try:
                extras["sky"] = sky_summary(watcher.sky.report())
            except Exception as exc:  # extras only
                log.debug("no sky for the widget: %s", exc)
        if outlook is not None:
            extras["best"] = best_time_text(best_time(watcher.store, cfg, now, outlook.hours()), cfg, now)
        if watcher.space:
            try:
                p = next((p for p in watcher.space.upcoming(hours=30) if p["visible"] and p["max_el"] >= 20
                          and p["start"] < now + 24 * 3600), None)
                if p:
                    extras["space"] = (f"The space station flies over {when_text(cfg, p['visible_from'] or p['start'], now)}, "
                                       f"from the {p['start_dir']}.")
            except Exception as exc:
                log.debug("no space station for the widget: %s", exc)
        active = [f for f in my_flights(watcher.store) if f["state"] in ("waiting", "airborne")]
        if active:
            f = active[0]
            extras["flight"] = f"{f['label']}: {'in the air' if f['state'] == 'airborne' else 'waiting to take off'}"
        relay.maybe_send(build_status(cfg, coming, infos, last[0] if last else None, now, extras))

    return job

