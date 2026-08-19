"""Public HTML pages."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import (auth, commanding, config, db, metrics, pktfilter, rbac,
               retention, search)
from .templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    rows = db.q("SELECT * FROM repeaters WHERE is_public=1 ORDER BY sort_order, name")
    cards = []
    for r in rows:
        latest = db.latest_for(r["id"])
        def val(m):
            row = latest.get(m)
            return None if row is None or row["value"] is None else row["value"]
        cards.append({
            # Dezelfde naam als /api/v1/repeaters teruggeeft. De startpagina en
            # die endpoint tonen dezelfde lijst; twee verschillende namen voor
            # één node zou de schakelaar meteen weer waardeloos maken.
            "slug": r["slug"], "name": db.public_name(r), "prefix": r["pubkey_prefix"],
            "last_seen": r["last_seen"],
            "online": val("online") == 1.0,
            "battery": val("battery_percentage"),
            "uptime": val("uptime"),
            "neighbors": val("neighbor_count"),
            "temperature": val("ch1_temperature") or val("ch2_temperature"),
        })
    # De filtercijfers van alle publieke nodes samen. Eén query voor de hele
    # tabel, en de optelsom zelf staat in pktfilter zodat de eerlijkheidsregels
    # -- hoeveel nodes tellen mee, en over welke periode -- op één plek staan en
    # niet uitgesmeerd raken over een sjabloon.
    filter_mesh = pktfilter.mesh_totals(db.filter_states_all(),
                                        [r["id"] for r in rows])
    return templates.TemplateResponse(request, "index.html", {
        "site_name": config.SITE_NAME, "cards": cards,
        "filter_mesh": filter_mesh,
        # Without a single placeable node the live map would be an empty grey
        # box, so the whole block (and Leaflet with it) stays out of the page.
        "has_livemap": bool(db.located_nodes()),
    })


@router.get("/pakketten", response_class=HTMLResponse)
def packets_page(request: Request):
    """The packet archive: query-bar search over everything still retained."""
    store = retention.overview()
    return templates.TemplateResponse(request, "packets.html", {
        "site_name": config.SITE_NAME,
        "span": db.packet_span(),
        "fields": search.describe_fields(),
        "sorts": search.describe_sorts(),
        "columns": search.describe_columns(),
        "retention_days": store["days"],
        # Only filled when a size ceiling cuts in before the period does. The
        # hint above this archive promises "packets are kept for N days", and
        # that promise stops being true the moment the FIFO bites: 12 days held
        # where 30 were configured. A page that leaves that out invites the
        # reader to conclude that nothing happened during a period that is
        # simply no longer in the database.
        "retention_effective": store["effective_days"] if store["falls_short"] else None,
    })


@router.get("/r/{slug}", response_class=HTMLResponse)
def repeater_page(request: Request, slug: str):
    r = db.qone("SELECT * FROM repeaters WHERE slug=? AND is_public=1", (slug,))
    if not r:
        raise HTTPException(404, "Onbekende repeater")
    latest = db.latest_for(r["id"])

    # Compute utilisation from the airtime totals ourselves: the value Home
    # Assistant reports drops back to 0 after every HA restart, until its own
    # measurement window has been rebuilt.
    computed = {
        "airtime_utilization": db.computed_utilization(r, "airtime"),
        "rx_airtime_utilization": db.computed_utilization(r, "rx_airtime"),
    }

    # De namen die een beheerder bij de kanalen van deze node gezet heeft. Het
    # telemetrieformaat draagt er geen, dus zonder deze tabel heet elk kanaal
    # alleen naar zijn nummer. Zie db.channel_names_for.
    ch_names = db.channel_names_for(r["id"])

    # tiles per section
    sections: dict[str, dict] = {}
    used = set()
    for key, title in metrics.SECTIONS:
        tiles = []
        for m in metrics.TILE_METRICS.get(key, []):
            row = latest.get(m)
            if row is None:
                continue
            # Ch1 voltage measures the same battery as 'bat' -- drop the duplicate tile
            if m == "ch1_voltage" and "bat" in latest:
                used.add(m)
                continue
            used.add(m)
            _, label, unit, _ = metrics.metric_info(m)
            tile = _tile(m, label, unit, row)
            if computed.get(m) is not None:
                tile["value"] = computed[m]
                tile["display"] = f"{computed[m]:g} {unit}" if unit else f"{computed[m]:g}"
            tiles.append(tile)
        extra = []
        for m, row in latest.items():
            if m in used:
                continue
            section, label, unit, sort = metrics.metric_info(m)
            if section != key:
                continue
            extra.append((sort, m, _channel_tile(m, label, unit, row, ch_names)))
        for _, m, t in sorted(extra, key=lambda x: x[0]):
            tiles.append(t)
            used.add(m)
        if tiles:
            sections[key] = {"key": key, "title": title, "tiles": tiles}

    # name from the neighbour sensor, falling back to the contacts table
    # (adverts) -- en met de zichtbaarheidskeuze erboven; zie db.neighbor_rows.
    neighbors = db.neighbor_rows(r["id"])
    charts = [
        {"key": key, "title": title, "metrics": mets, "hours": hours,
         "labels": [metrics.metric_info(m)[1] for m in mets],
         "unit": metrics.metric_info(mets[0])[2]}
        for key, title, mets, hours in metrics.CHARTS
        if any(m in latest for m in mets)
    ]

    # Eén grafiek per generic-sensorkanaal, in plaats van een vaste lijst zoals
    # metrics.CHARTS. Welke kanalen een node heeft weet alleen die node, dus een
    # vaste lijst zou per dienst uitgebreid moeten worden en zou bij een nieuwe
    # dienst stil niets tonen. Een uptimemonitor zet hier de pingtijd, en dat is
    # een tijdreeks zoals elke andere -- ze hoort ook zo getekend te worden.
    #
    # Alleen generic sensors: een switch is 0/1 en daar is een lijndiagram geen
    # goede vorm voor. Die staat als tegel in de kanalensectie en blijft
    # aanklikbaar, dus zijn historiek is niet weg.
    for m in sorted(latest, key=lambda n: (metrics.channel_metric(n) or (0, ""))[0]):
        ch = metrics.channel_metric(m)
        if ch is None or ch[1] != "generic":
            continue
        channel, kind = ch
        named = ch_names.get(channel)
        label = metrics.channel_label(channel, kind, named["name"] if named else None)
        charts.append({
            "key": m, "title": f"{label} (24 u)", "metrics": [m], "hours": 24,
            "labels": [label],
            "unit": metrics.channel_unit(kind, named["unit"] if named else None),
        })

    # configurable block order and history ranges
    layout = metrics.parse_layout(db.get_setting("layout"))
    ranges = metrics.parse_ranges(db.get_setting("history_ranges"))

    # Wie er meekijkt wordt hier bepaald en niet pas onderaan, want de
    # uitsplitsing van het pakketfilter hangt ervan af: de tellingen per
    # pakkettype zijn openbaar, de geblokkeerde kanalen niet. Zie
    # pktfilter.breakdown() voor waar die grens ligt en waarom.
    session_cookie = request.cookies.get(auth.SESSION_COOKIE, "")
    ingelogd = auth.read_session(session_cookie)
    is_admin = ingelogd is not None
    filter_stats = pktfilter.breakdown(db.filter_state_for(r["id"]), is_admin)

    blocks = []
    for item in layout:
        if not item["visible"]:
            continue
        key = item["key"]
        if key == "charts" and charts:
            blocks.append({"type": "charts", "charts": charts})
        elif key == "map" and neighbors and db.contact_location(r["pubkey_prefix"][:6]):
            blocks.append({"type": "map"})
        elif key == "neighbors" and neighbors:
            blocks.append({"type": "neighbors"})
        elif key in sections:
            blocks.append({"type": "section", "section": sections[key]})
        # De uitsplitsing hangt aan het filterblok in plaats van een eigen plek
        # in de indeling te krijgen: het is dezelfde vraag als de tegels erboven,
        # alleen uitgesplitst, en twee losse blokken die je uit elkaar kunt
        # slepen zouden die samenhang alleen maar kunnen breken.
        if key == "filter" and filter_stats["bekend"]:
            blocks.append({"type": "filterstats", "stats": filter_stats})

    online_row = latest.get("online")
    # De opvraagknop staat op deze publieke pagina voor wie ingelogd is. Sinds
    # toegang niet meer alles-of-niets is, is "ingelogd" niet meer hetzelfde als
    # "mag deze node uitvragen": iemand kan lezer zijn op deze node, of er
    # helemaal niets over mogen. De knop verdwijnt daar niet van -- hij staat uit
    # met de reden erbij, zoals overal op deze site -- dus de beslissing reist
    # mee naar de sjabloon in plaats van dat de klik in een 403 eindigt.
    mag_uitvragen = rbac.decide(ingelogd, "node.uitvragen", r) if ingelogd else None
    return templates.TemplateResponse(request, "repeater.html", {
        "site_name": config.SITE_NAME, "r": r, "blocks": blocks,
        # De naam apart naast de rij, en de template gebruikt uitsluitend deze.
        # ``r`` blijft er ongeschonden in staan omdat er nog een dozijn kolommen
        # uit gelezen worden; er één van vervangen zou een rij opleveren die
        # half waar is, en dat is een val voor de volgende lezer.
        "display_name": db.public_name(r),
        "neighbors": neighbors, "gauges": metrics.GAUGES, "thermos": metrics.THERMOMETERS,
        "ranges": [{"hours": h, "label": metrics.range_label(h)} for h in ranges],
        "default_hours": 24 if 24 in ranges else ranges[0],
        "is_online": online_row is not None and online_row["value"] == 1.0,
        "is_admin": is_admin,
        "mag_uitvragen": mag_uitvragen,
        "csrf": auth.csrf_token(session_cookie) if is_admin else "",
        # 'mqtt' | 'queued' | 'both' | 'none', en '1' uit oudere links. Wat er
        # werkelijk gebeurd is, want de knop kan tegenwoordig ook nergens heen,
        # en dan hoort de pagina dat te zeggen in plaats van een update te
        # beloven die niemand gaat halen.
        "refresh_state": ("both" if request.query_params.get("refresh") == "1"
                          else request.query_params.get("refresh", "")),
        # Alleen voor beheerders berekend: de knop staat er voor niemand anders.
        "route": commanding.describe(r) if is_admin else None,
        "zones": {
            m: {"min": cfg[0], "max": cfg[1], "segments": cfg[2]}
            for m, cfg in {**metrics.GAUGES, **metrics.THERMOMETERS}.items()
        },
    })


def _channel_tile(metric: str, label: str, unit: str | None, row, ch_names) -> dict:
    """Een tegel, met voor kanaalmetingen de naam die de beheerder gaf.

    Voor alles wat geen kanaalmeting is verandert er niets. Voor een kanaalmeting
    komt de naam uit ``channel_names`` voor het soortlabel te staan, en bij een
    generic sensor mag de beheerder ook de eenheid zetten -- het LPP-type zegt
    daar niets over (4 byte, vermenigvuldiger 1) en "12" zonder "ms" erachter is
    een getal zonder betekenis.
    """
    ch = metrics.channel_metric(metric)
    if ch is None:
        return _tile(metric, label, unit, row)
    channel, kind = ch
    named = ch_names.get(channel)
    label = metrics.channel_label(channel, kind, named["name"] if named else None)
    unit = metrics.channel_unit(kind, named["unit"] if named else None)
    tile = _tile(metric, label, unit, row)
    # Een switch is 0 of 1 en dat is geen getal om te lezen. 'op'/'neer' is wat
    # het betekent: de dienst antwoordt, of hij antwoordt niet.
    if kind == "switch" and tile["value"] is not None:
        tile["display"] = "op" if tile["value"] == 1.0 else "neer"
        tile["i18n"] = "state.up" if tile["value"] == 1.0 else "state.down"
        tile["i18n_vars"] = {}
    # De tegel is aanklikbaar op zijn metricnaam, en daarmee heeft elke pingtijd
    # dezelfde historiek als elke andere meting -- zonder dat er per dienst een
    # grafiek bijgeprogrammeerd hoeft te worden.
    tile["channel"] = channel
    tile["kind"] = kind
    return tile


def _tile(metric: str, label: str, unit: str | None, row) -> dict:
    value = row["value"]
    display = row["value_str"] or "—"
    # Most tile values are numbers and read the same in every language; uptime is
    # the exception, so it carries a translation key alongside its Dutch text.
    i18n_key = i18n_vars = None
    if value is not None:
        if metric == "online":
            display = "Online" if value == 1.0 else "Offline"
        elif metric == "uptime":
            display, i18n_key, i18n_vars = _fmt_uptime(value)
        elif value == int(value) and abs(value) < 1e9:
            display = f"{int(value):,}".replace(",", " ")
        else:
            display = f"{value:g}"
        if unit and metric not in ("online", "uptime"):
            display += f" {unit}"
    hint_key, hint_text = metrics.HINTS.get(metric, (None, None))
    return {"metric": metric, "label": label, "value": value, "display": display,
            "ts": row["ts"], "i18n": i18n_key, "i18n_vars": i18n_vars,
            "hint": hint_text, "hint_key": hint_key}


def _fmt_uptime(days: float) -> tuple[str, str, dict]:
    """Dutch rendering plus the translation key and values behind it."""
    total_min = int(days * 24 * 60)
    d, rest = divmod(total_min, 24 * 60)
    h, m = divmod(rest, 60)
    if d:
        return f"{d} d {h} u", "fmt.uptime_dh", {"d": d, "h": h}
    if h:
        return f"{h} u {m} min", "fmt.uptime_hm", {"h": h, "m": m}
    return f"{m} min", "fmt.uptime_m", {"m": m}
