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
    "trackers.serial": ("Instellen via USB", "Een tracker via Web Serial uitlezen en instellen."),
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
    {"name": "Operators", "perms": [p for p in PERMS if p not in ("users.manage", "system.manage")], "all_trackers": True,
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


def principal_for_user(user: dict[str, Any], group: dict[str, Any]) -> Principal:
    return Principal(
        name=user["username"], display=user["display_name"] or user["username"], kind="user",
        group=group["name"], perms=set(group["perms"]) & set(PERMS),
        tracker_ids=None if group["all_trackers"] else set(group["trackers"]),
        history_hours=int(group["history_hours"] or 0), user_id=user["id"])


def principal_for_share(share: dict[str, Any]) -> Principal:
    return Principal(
        name=f"deellink:{share['name']}", display=share["name"], kind="share", group="deellink",
        perms=set(SHARE_PERMS) | ({"map.sidebar"} if share.get("sidebar") else set()),
        tracker_ids=set(share["trackers"]), history_hours=int(share["hours"] or 12))
