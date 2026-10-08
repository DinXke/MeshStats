"""Gebruikers, groepen en rechten (RBAC).

Een gebruiker zit in één of meer groepen. Een groep heeft:
- een set rechten (PERMS),
- per kanaal een niveau: "kaart" (de trackers van dat kanaal op de kaart, in het logboek en
  in exports) of "sleutel" (ook naam, sleutel en QR van het kanaal, om het op een eigen
  companion te zetten), of "alle kanalen" (all_trackers) op kaartniveau,
- eventueel losse trackers,
- een maximale terugblik in uren (0 = onbeperkt).

Een tracker hoort bij één trackingkanaal (trackers.channel_id); wie dat kanaal mag lezen,
ziet de tracker. Naast gebruikers bestaan er deellinks: een geheime URL die zonder login een
kioskkaart toont voor gekozen kanalen of trackers, tot een vervaldatum. Die worden als een
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
    "keys.manage":     ("Sleutels en back-ups", "Privésleutels op de server maken, toestellen klaarmaken en back-ups (met privésleutel) bewaren en terugzetten."),
    "sims.manage":     ("Simulators", "Virtuele trackers starten, wijzigen en stoppen."),
    "companion.view":  ("Server-companion", "QR-code, pubkey en contacten van de companion."),
    "export":          ("Exporteren", "Sporen downloaden als GPX of CSV."),
    "share.manage":    ("Deellinks", "Kaartlinks zonder login maken en intrekken."),
    "alerts.manage":   ("Meldingsregels (gedeeld)", "Gedeelde regels die bij gebeurtenissen een DM via de mesh sturen."),
    "alerts.personal": ("Eigen meldingsregels", "Persoonlijke meldingsregels (sturen ook DM's over de mesh)."),
    "system.manage":   ("Systeeminstellingen", "Vaste instellingen, kanalen (met alle sleutels), meldingen, bewaartermijn, simulator."),
    "users.manage":    ("Gebruikers en groepen", "Gebruikers, groepen, rechten en het auditlog beheren."),
}

LEVELS = ("kaart", "sleutel")       # oplopend: sleutel omvat kaart

DEFAULT_GROUPS: list[dict[str, Any]] = [
    {"name": "Beheerders", "perms": list(PERMS), "all_trackers": True, "history_hours": 0,
     "description": "Alles, inclusief gebruikers, groepen en alle kanaalsleutels."},
    {"name": "Operators", "perms": [p for p in PERMS if p not in ("users.manage", "system.manage", "keys.manage")], "all_trackers": True,
     "history_hours": 0, "description": "Dagelijks werk: trackers, zones, simulators en deellinks."},
    {"name": "Kijkers", "perms": ["map.view", "map.sidebar", "map.tracks", "map.nodes", "zones.view", "log.view"],
     "all_trackers": True, "history_hours": 24 * 7, "description": "Meekijken met lijst en sporen, niets wijzigen."},
    {"name": "Kiosk", "perms": ["map.view", "map.tracks"], "all_trackers": True, "history_hours": 12,
     "description": "Enkel de kaart, schermvullend, bv. op een groot scherm in de post."},
]

SHARE_PERMS = {"map.view", "map.tracks"}


def stronger(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Het hoogste van twee kanaalniveaus (None = geen)."""
    if a not in LEVELS:
        return b if b in LEVELS else None
    if b not in LEVELS:
        return a
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


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
    channels: dict[int, str] = field(default_factory=dict)   # kanaal-id -> "kaart" | "sleutel"
    all_channels: bool = False      # alle kanalen op kaartniveau

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def sees(self, tracker_id: int) -> bool:
        return self.tracker_ids is None or tracker_id in self.tracker_ids

    def channel_level(self, cid: Optional[int]) -> Optional[str]:
        """Niveau op een kanaal: beheerders (system.manage) 'sleutel' op alles."""
        if cid is None:
            return None
        if self.can("system.manage"):
            return "sleutel"
        lvl = self.channels.get(cid)
        return stronger(lvl, "kaart" if self.all_channels else None)

    def sees_channel(self, cid: Optional[int]) -> bool:
        return self.channel_level(cid) is not None

    def knows_key(self, cid: Optional[int]) -> bool:
        return self.channel_level(cid) == "sleutel"

    def max_hours(self, wanted: float) -> float:
        return wanted if self.history_hours <= 0 else min(wanted, float(self.history_hours))

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "display": self.display, "kind": self.kind, "group": self.group,
                "perms": sorted(self.perms), "history_hours": self.history_hours,
                "all_trackers": self.tracker_ids is None, "all_channels": self.all_channels,
                "channels": {str(k): v for k, v in self.channels.items()}}


def principal_for_user(user: dict[str, Any], groups, channel_members: Optional[dict[int, set[int]]] = None) -> Principal:
    """Een gebruiker in één of meer groepen krijgt de som: alle rechten samen, per kanaal het
    hoogste niveau, alle trackers van die kanalen plus losse trackers, en de ruimste terugblik
    (0 = onbeperkt wint). "Alle kanalen" (all_trackers) = alle trackers, ook zonder kanaal."""
    if isinstance(groups, dict):
        groups = [groups]
    members = channel_members or {}
    perms: set[str] = set()
    tids: Optional[set[int]] = set()
    hours: list[int] = []
    chans: dict[int, str] = {}
    all_ch = False
    for g in groups:
        perms |= set(g["perms"])
        hours.append(int(g["history_hours"] or 0))
        for cid, lvl in (g.get("channels") or {}).items():
            cid = int(cid)
            best = stronger(chans.get(cid), lvl)
            if best:
                chans[cid] = best
        if g["all_trackers"]:
            all_ch = True
            tids = None
        elif tids is not None:
            tids |= set(g.get("trackers") or [])
    if tids is not None:
        for cid in chans:
            tids |= members.get(cid, set())
    history = 0 if not hours or 0 in hours else max(hours)
    return Principal(
        name=user["username"], display=user["display_name"] or user["username"], kind="user",
        group=", ".join(g["name"] for g in groups), perms=perms & set(PERMS),
        tracker_ids=tids, history_hours=history, user_id=user["id"], channels=chans, all_channels=all_ch)


def principal_for_share(share: dict[str, Any], channel_members: Optional[dict[int, set[int]]] = None,
                        creator: Optional[Principal] = None) -> Principal:
    """Losse trackers van de link, plus de huidige trackers van zijn kanalen. Trackers via
    een kanaal alleen als de maker ze (nog) mag zien: een link toont nooit meer dan wie hem
    maakte."""
    ids = set(share["trackers"])
    members = channel_members or {}
    for cid in share.get("channels") or []:
        ids |= {t for t in members.get(cid, set()) if creator is not None and creator.sees(t)}
    return Principal(
        name=f"deellink:{share['name']}", display=share["name"], kind="share", group="deellink",
        perms=set(SHARE_PERMS) | ({"map.sidebar"} if share.get("sidebar") else set()),
        tracker_ids=ids, history_hours=int(share["hours"] or 12))
