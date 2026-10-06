"""Systeeminstellingen die in de webinterface te wijzigen zijn.

config.yaml levert de startwaarden; wat een beheerder in het tabblad Systeem
wijzigt, staat in de database en wint. Elke instelling heeft een type, grenzen
en een uitleg (die ook in de webinterface getoond wordt).
"""
from __future__ import annotations

from typing import Any

# key -> (standaard, min, max, label, uitleg)
SPEC: dict[str, tuple[Any, Any, Any, str, str]] = {
    "alert_gap_s":         (15, 3, 600, "Tijd tussen meldingen (s)",
                            "Wachttijd tussen twee DM's, zodat veel ontvangers de mesh niet overspoelen."),
    "alert_attempts":      (3, 1, 6, "Pogingen per melding",
                            "Hoe vaak een DM opnieuw verstuurd wordt zonder ACK (zoals de app)."),
    "alert_flood_after":   (2, 1, 6, "Flood vanaf poging",
                            "Vanaf deze poging gaat de DM via flood in plaats van het gekende pad."),
    "alert_rounds":        (1, 1, 5, "Rondes",
                            "Lukt een melding na alle pogingen niet, hoeveel keer dan later opnieuw (x keer y pogingen)."),
    "alert_round_pause_s": (120, 10, 3600, "Pauze tussen rondes (s)", "Wachttijd voor een volgende ronde."),
    "alert_sims":          (False, None, None, "Meldingen voor virtuele trackers",
                            "Ook meldingen sturen voor simulators. Uit laten: anders belast een simulator de mesh."),
    "silent_alert_h":      (0, 0, 168, "Te lang stil na (uur)",
                            "Gebeurtenis 'te lang stil' als een tracker zo lang niets stuurde (0 = uit)."),
    "stale_after_h":       (25, 1, 720, "Grijs op de kaart na (uur)", "Een tracker die zo lang stil is, toont grijs."),
    "retention_days":      (90, 1, 3650, "Bewaartermijn (dagen)", "Posities ouder dan dit worden automatisch gewist."),
    "sim_history_days":    (7, 0, 30, "Historiek simulators (dagen)",
                            "Een nieuwe simulator krijgt meteen zo veel dagen gesimuleerde geschiedenis (0 = geen)."),
}


def defaults_from_config(cfg: Any) -> dict[str, Any]:
    d = {k: v[0] for k, v in SPEC.items()}
    d["retention_days"] = int(cfg.retention_days)
    d["stale_after_h"] = max(1, round(cfg.stale_after_s / 3600))
    return d


def effective(cfg: Any, stored: dict[str, Any]) -> dict[str, Any]:
    out = defaults_from_config(cfg)
    for k, v in stored.items():
        if k in SPEC:
            out[k] = v
    return out


def validate(changes: dict[str, Any]) -> dict[str, Any]:
    """Alleen gekende sleutels, juist type, binnen de grenzen. ValueError anders."""
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in SPEC:
            raise ValueError(f"onbekende instelling {k}")
        default, lo, hi, label, _ = SPEC[k]
        if isinstance(default, bool):
            out[k] = bool(v)
            continue
        try:
            n = type(default)(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: geen getal")
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            raise ValueError(f"{label}: tussen {lo} en {hi}")
        out[k] = n
    return out


def describe() -> list[dict[str, Any]]:
    return [{"key": k, "default": v[0], "min": v[1], "max": v[2], "label": v[3], "help": v[4],
             "type": "bool" if isinstance(v[0], bool) else "number"} for k, v in SPEC.items()]
