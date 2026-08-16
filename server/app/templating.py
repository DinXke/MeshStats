import time
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Undefined
from markupsafe import Markup

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Cache busting: changes on every (re)start, and therefore on every deploy
templates.env.globals["asset_v"] = str(int(time.time()))


def mag_attr(besluit) -> Markup:
    """Een ``rbac.Besluit`` als knopattributen: leeg bij ja, uitgeschakeld bij nee.

    Bestaat omdat de firmwarepagina per node een handvol knoppen tekent en het
    ``{% if %}``-blok eromheen elke keer hetzelfde zou zijn. Eén filter is één
    plek waar de reden in de tooltip terechtkomt, in plaats van vijf plaatsen om
    er één te vergeten -- en vergeten betekent hier een knop die er werkend
    uitziet voor iemand die hem niet mag indrukken.

    Een ontbrekend besluit levert geen attributen op, en dat is met opzet niet
    'uitgeschakeld': zo'n knop staat onder een sjabloon dat zonder rechten
    gerenderd wordt, en die weg loopt hoe dan ook langs de rechtencontrole in de
    route. De sjabloon is de beleefdheid, de route is de grendel.

    Er wordt naar het veld ``allowed`` gekeken en niet naar de waarheid van het
    besluit zelf; zie rbac.Besluit voor waarom die twee uit elkaar gehouden
    worden.

    Undefined wordt met ``isinstance`` afgevangen en niet met een standaard op
    ``getattr``: een Jinja-Undefined gooit bij elke attribuuttoegang een
    UndefinedError in plaats van een AttributeError, en dan komt die standaard
    nooit aan bod.
    """
    if besluit is None or isinstance(besluit, Undefined):
        return Markup("")
    if besluit.allowed:
        return Markup("")
    reden = str(getattr(besluit, "reason", "")).replace('"', "&quot;")
    return Markup(f' disabled title="{reden}"')


templates.env.filters["mag_attr"] = mag_attr
