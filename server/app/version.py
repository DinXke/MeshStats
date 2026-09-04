"""Welke MeshManager draait hier.

Twee getallen, met opzet twee:

``VERSION``        de semantische versie van de site, met de hand opgehoogd bij
                   een wijziging die een gebruiker merkt. Dit is het getal voor
                   mensen: "sinds 1.1.0 kan X".
``sha`` / ``date`` de git-commit en de bouwdatum, ingebakken bij de Docker-build
                   via MM_BUILD_SHA / MM_BUILD_DATE (zie Dockerfile en
                   deploy/autoupdate.sh). Dit is het getal voor het zoeken van
                   een fout: twee sites met dezelfde VERSION kunnen een andere
                   commit draaien, en dan is de commit het enige dat zegt welke.

Waarom niet alleen de commit: een hash zegt niets over afstand ("hoeveel is er
veranderd sinds toen") en niets over compatibiliteit. Waarom niet alleen de
versie: die wordt vergeten op te hogen, en dan liegt hij. Samen dekken ze
elkaars gat, en de footer toont ze naast elkaar.

Buiten Docker (een dev-checkout) komt de commit uit ``git`` als dat er is; lukt
dat niet, dan staat er ``dev`` -- nooit een verzonnen waarde, om dezelfde reden
als overal in dit project: een getal dat er echt uitziet maar niets meet, is
erger dan een leeg vak.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Begonnen op 2.10.0 (2026-09-04), niet op 1.0.0: de site en de node-firmware
# (MeshManagerNet, toen ook 2.10.0) komen uit dezelfde repo en dezelfde
# generatie -- de "2" is MeshManager sinds de hernoeming van MeshStats, en de
# site heeft elke firmware-stap sinds 2026-08-14 meegemaakt (200 commits). Vanaf
# hier telt de server zelfstandig: MINOR bij een merkbare functie, PATCH bij een
# fix, MAJOR bij een breuk in de API of de databank. Elke stap krijgt een regel
# in CHANGELOG.md.
VERSION = "2.10.0"

# Alleen hex en een redelijke lengte: de waarde komt uit de omgeving van de
# container en gaat de HTML in. Niets anders dan een commit-hash hoort daar.
_SHA = re.compile(r"^[0-9a-f]{4,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _git_short() -> str:
    """De korte commit van de checkout waar deze module in staat, of ''."""
    try:
        top = Path(__file__).resolve().parents[2]
        uit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=top,
                             capture_output=True, text=True, timeout=3)
        sha = uit.stdout.strip().lower()
        return sha if uit.returncode == 0 and _SHA.match(sha) else ""
    except Exception:
        return ""


def info() -> dict:
    """Versie, commit, bouwdatum en een kant-en-klaar label voor de footer."""
    sha = os.environ.get("MM_BUILD_SHA", "").strip().lower()
    if not _SHA.match(sha):
        sha = _git_short() or "dev"
    date = os.environ.get("MM_BUILD_DATE", "").strip()
    if not _DATE.match(date):
        date = ""
    label = "v%s · %s" % (VERSION, sha)
    if date:
        label += " · %s" % date
    return {"version": VERSION, "sha": sha, "date": date, "label": label}
