"""Gebruikers, groepen en rechten (RBAC).

Een gebruiker zit in één groep. Een groep heeft:
- een set rechten (PERMS),
- een trackerbereik: alle trackers, of een vaste lijst,
- een maximale terugblik in uren (0 = onbeperkt).

Naast gebruikers bestaan er deellinks: een geheime URL die zonder login een
kioskkaart toont voor enkele trackers, tot een vervaldatum. Die worden als een
"principal" met beperkte rechten behandeld, zodat dezelfde controles gelden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# id -> (korte naam, uitleg). Volgorde = volgorde in het beheerscherm.
PERMS: dict[str, tuple[str, str]] = {
    "map.view":        ("Kaart bekijken", "De live kaart openen."),
    "map.sidebar":     ("Zijbalk en lijst", "Zonder dit recht toont de kaart enkel de trackers (kioskweergave)."),
    "map.tracks":      ("Sporen", "Afgelegde sporen en spoorpunten tonen."),
    "map.nodes":       ("Meshnodes", "Repeaters, rooms en companions uit de mesh op de kaart."),
    "map.details":     ("Technische details", "Pubkey, SNR, hops en batterij in popups en lijsten."),
    "log.view":        ("Logboek", "Alle gebeurtenissen per tracker bekijken en filteren."),
    "zones.view":      ("Zones bekijken", "Gedeelde zones en eigen zones op de kaart, met hun meldingen."),
    "zones.manage":    ("Gedeelde zones beheren", "Gedeelde zones tekenen, wijzigen en verwijderen (eigen zones mag iedereen met 'Zones bekijken')."),
    "trackers.manage": ("Trackers beheren", "Trackers toevoegen, bewerken en verwijderen."),
    "trackers.serial": ("Instellen via USB", "Een tracker via Web Serial uitlezen, instellen en flashen."),
    "keys.manage":     ("Sleutels en backups", "Privésleutels op de server maken, toestellen klaarmaken en backups (met privésleutel) bewaren en terugzetten."),
    "sims.manage":     ("Simulators", "Virtuele trackers starten, wijzigen en stoppen."),
    "companion.view":  ("Server-companion", "QR-code, pubkey en contacten van de companion."),
    "export":          ("Exporteren", "Sporen downloaden als GPX of CSV."),
    "share.manage":    ("Deellinks", "Kaartlinks zonder login maken en intrekken."),
    "alerts.manage":   ("Meldingsregels (gedeeld)", "Gedeelde regels die bij gebeurtenissen een DM via de mesh sturen."),
    "alerts.personal": ("Eigen meldingsregels", "Persoonlijke meldingsregels (sturen ook DM's over de mesh)."),
    "system.manage":   ("Systeeminstellingen", "Vaste instellingen: meldingen, bewaartermijn, simulator."),
    "users.manage":    ("Gebruikers en groepen", "Gebruikers, groepen, rechten en het auditlog beheren."),
}

DEFAULT_GROUPS: list[dict[str, Any]] = [
    {"name": "Beheerders", "perms": list(PERMS), "all_trackers": True, "history_hours": 0,
     "description": "Alles, inclusief gebruikers en groepen."},
    {"name": "Operators", "perms": [p for p in PERMS if p not in ("users.manage", "system.manage", "keys.manage")], "all_trackers": True,
     "history_hours": 0, "description": "Dagelijks werk: trackers, zones, simulators en deellinks."},
    {"name": "Kijkers", "perms": ["map.view", "map.sidebar", "map.tracks", "map.nodes", "zones.view", "log.view"],
     "all_trackers": True, "history_hours": 24 * 7, "description": "Meekijken met lijst en sporen, niets wijzigen."},
    {"name": "Kiosk", "perms": ["map.view", "map.tracks"], "all_trackers": True, "history_hours": 12,
     "description": "Enkel de kaart, schermvullend, bv. op een groot scherm in de post."},
]

SHARE_PERMS = {"map.view", "map.tracks"}


@dataclass
class Principal:
    name: str
    display: str
    kind: str                       # user | share
    group: str
    perms: set[str] = field(default_factory=set)
    tracker_ids: Optional[set[int]] = None    # None = alle trackers
    history_hours: int = 0          # 0 = onbeperkt
    user_id: Optional[int] = None
    tracker_groups: set = field(default_factory=set)   # trackergroepen die zijn groepen expliciet zien

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def sees(self, tracker_id: int) -> bool:
        return self.tracker_ids is None or tracker_id in self.tracker_ids

    def max_hours(self, wanted: float) -> float:
        return wanted if self.history_hours <= 0 else min(wanted, float(self.history_hours))

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "display": self.display, "kind": self.kind, "group": self.group,
                "perms": sorted(self.perms), "history_hours": self.history_hours,
                "all_trackers": self.tracker_ids is None}


def principal_for_user(user: dict[str, Any], groups, tracker_group_members: Optional[dict[int, set[int]]] = None) -> Principal:
    """Een gebruiker in één of meer groepen krijgt de som: alle rechten samen, alle
    trackers die één van zijn groepen ziet (los gekozen of via een trackergroep), en de
    ruimste terugblik (0 = onbeperkt wint)."""
    if isinstance(groups, dict):
        groups = [groups]
    members = tracker_group_members or {}
    perms: set[str] = set()
    tids: Optional[set[int]] = set()
    hours: list[int] = []
    tgs: set[int] = set()
    for g in groups:
        perms |= set(g["perms"])
        tgs |= set(g.get("tracker_groups") or [])
        hours.append(int(g["history_hours"] or 0))
        if g["all_trackers"]:
            tids = None
        elif tids is not None:
            tids |= set(g["trackers"])
            for tg in g.get("tracker_groups") or []:
                tids |= members.get(tg, set())
    history = 0 if not hours or 0 in hours else max(hours)
    return Principal(
        name=user["username"], display=user["display_name"] or user["username"], kind="user",
        group=", ".join(g["name"] for g in groups), perms=perms & set(PERMS),
        tracker_ids=tids, history_hours=history, user_id=user["id"], tracker_groups=tgs)


def principal_for_share(share: dict[str, Any], tracker_group_members: Optional[dict[int, set[int]]] = None,
                        creator: Optional[Principal] = None) -> Principal:
    """Losse trackers van de link, plus de huidige leden van zijn trackergroepen. Leden
    via een groep alleen als de maker ze (nog) mag zien: een link toont nooit meer dan
    wie hem maakte."""
    ids = set(share["trackers"])
    members = tracker_group_members or {}
    for tg in share.get("tracker_groups") or []:
        ids |= {t for t in members.get(tg, set()) if creator is not None and creator.sees(t)}
    return Principal(
        name=f"deellink:{share['name']}", display=share["name"], kind="share", group="deellink",
        perms=set(SHARE_PERMS) | ({"map.sidebar"} if share.get("sidebar") else set()),
        tracker_ids=ids, history_hours=int(share["hours"] or 12))
