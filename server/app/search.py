"""Query language for the packet archive.

The search page offers a Kibana-style query bar, so this module turns what a
visitor types into a SQL WHERE clause. It is the whole language: there is no
second, hidden set of rules elsewhere.

Syntax
------
::

    type:ADVERT                     one field equals one value
    type:ADVERT scope:scoped        several clauses, all of which must hold
    sender:2ae7*                    trailing wildcard
    snr:>5  rssi:<=-100             comparison, numeric fields only
    len:20..40                      range, numeric fields only
    name:"BE-HSS-JessaZH.VIR"       quotes, for a value with spaces in it
    -type:ACK       NOT type:ACK    negation, either spelling
    2ae7                            a bare word: searched across the text fields
    type:(ADVERT OR TXT_MSG)        one field, several accepted values

Clauses are joined with AND, which is what Kibana does when you separate them
with spaces. OR exists only inside the parentheses of a single field, which
covers "one of these types" without turning this into an expression parser whose
precedence rules nobody would remember.

Design rules
------------
Pure functions, no I/O, no database handle. ``parse`` returns a ``Query`` with
the SQL fragment and its parameters, or raises ``QueryError`` with a message
meant for the person who typed it.

Nothing is ever silently dropped. An unknown field name, a comparison on a text
column, a malformed range: each one is an error the page shows, never a clause
quietly skipped. A search that ignores half of what you asked for while
reporting a confident number of hits is worse than one that refuses to run.
"""
import re

# Placeholder columns are interpolated into SQL directly, so nothing here may
# come from user input -- only the fixed strings below.
_TEXT = "text"
_NUM = "num"
_TS = "ts"


class Field:
    """One searchable column: how it is spelled, and what it accepts."""

    def __init__(self, sql: str, kind: str, label: str, hint: str = "",
                 facet: bool = False):
        self.sql = sql          # SQL expression, from this module only
        self.kind = kind        # _TEXT, _NUM or _TS
        self.label = label      # heading in the field list
        self.hint = hint        # example value, shown in the help
        self.facet = facet      # worth offering a "top values" breakdown


# The joins these expressions assume are in db.search_packets; keep the two in
# step. Sender and observer each get a name of their own as well as a key, since
# a visitor knows nodes by one or the other depending on which they saw last.
FIELDS: dict[str, Field] = {
    "type":     Field("p.payload_name", _TEXT, "Payloadtype", "ADVERT", facet=True),
    "route":    Field("p.route", _TEXT, "Routetype", "FLOOD", facet=True),
    "scope":    Field("p.scope", _TEXT, "Bereik", "scoped", facet=True),
    "region":   Field("p.scope_region", _NUM, "Regio", "7", facet=True),
    "sender":   Field("p.sender", _TEXT, "Afzender (sleutel)", "2ae7c1", facet=True),
    "observer": Field("p.observer", _TEXT, "Waarnemer (sleutel)", "2ae7c1d40f93", facet=True),
    "name":     Field("COALESCE(c.name, '') || ' ' || COALESCE(o.name, '')", _TEXT,
                      "Naam van afzender of waarnemer", "BE-HSS"),
    "country":  Field("COALESCE(c.country, o.country)", _TEXT, "Land", "BE", facet=True),
    "snr":      Field("p.snr", _NUM, "SNR", ">5"),
    "rssi":     Field("p.rssi", _NUM, "RSSI", "<-100"),
    "len":      Field("p.len", _NUM, "Lengte in bytes", "20..40"),
    "hops":     Field("p.path_len", _NUM, "Aantal hops", "0", facet=True),
    "path":     Field("p.path", _TEXT, "Hop in het pad", "2ae7"),
    "hash":     Field("p.phash", _TEXT, "Payloadhash", ""),
}

# What a bare word searches. Deliberately the identifying columns only: adding
# snr would mean typing "5" matched a signal strength, which is never what
# somebody means by a loose word in a search box.
FREE_TEXT_FIELDS = ("p.sender", "p.observer", "p.payload_name", "p.scope",
                    "c.name", "o.name", "c.country", "o.country")

# scope_region is stored inside scope_codes rather than as a column of its own,
# so searching on it needs the same derivation the API does. Kept next to the
# field table because the two have to agree on what "region" means.
REGION_SQL = ("CAST(NULLIF(substr(p.scope_codes, instr(p.scope_codes, ',') + 1), '0') "
              "AS INTEGER)")

_COMPARISONS = (("<=", "<="), (">=", ">="), ("<", "<"), (">", ">"))
_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)$")


class QueryError(ValueError):
    """A query that cannot be run, with a message for the person who typed it."""


class Query:
    def __init__(self, sql: str, params: list):
        self.sql = sql          # "" when the query is empty: match everything
        self.params = params


def parse(text: str) -> Query:
    """Turn a query string into a WHERE fragment. Raises QueryError."""
    clauses, params = [], []
    for negated, field, value in _tokenize(text or ""):
        sql, vals = (_free_text(value) if field is None
                     else _field_clause(field, value))
        clauses.append(f"NOT ({sql})" if negated else sql)
        params.extend(vals)
    return Query(" AND ".join(clauses), params)


def _tokenize(text: str) -> list[tuple[bool, str | None, str]]:
    """Split a query into (negated, field or None, value) triples.

    Hand-written rather than a regex over the whole string: quotes and the
    parenthesised OR list both contain spaces, and a single expression that
    handles those is write-only.
    """
    out: list[tuple[bool, str | None, str]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue

        negated = False
        if text[i] == "-":
            negated, i = True, i + 1
        elif text[i:i + 4].upper() == "NOT ":
            negated, i = True, i + 4
            while i < n and text[i].isspace():
                i += 1
        if i >= n:
            raise QueryError("Er staat een min of NOT zonder iets erachter.")

        # A field name, if this token has one: letters up to a colon.
        field = None
        match = re.match(r"([A-Za-z_]+):", text[i:])
        if match:
            field = match.group(1).lower()
            i += match.end()
            if field not in FIELDS:
                known = ", ".join(sorted(FIELDS))
                raise QueryError(f"Onbekend veld '{field}'. Bekende velden: {known}.")

        value, i = _read_value(text, i, field)
        if not value:
            raise QueryError(f"Veld '{field}' heeft geen waarde." if field
                             else "Lege zoekterm.")
        out.append((negated, field, value))
    return out


def _read_value(text: str, i: int, field: str | None) -> tuple[str, int]:
    """Read one value: quoted, parenthesised, or up to the next space."""
    n = len(text)
    if i < n and text[i] == '"':
        end = text.find('"', i + 1)
        if end < 0:
            raise QueryError("Een aanhalingsteken is niet afgesloten.")
        return text[i + 1:end], end + 1
    if i < n and text[i] == "(":
        end = text.find(")", i + 1)
        if end < 0:
            raise QueryError("Een haakje is niet gesloten.")
        if field is None:
            raise QueryError("Haakjes horen bij een veld, zoals type:(ADVERT OR ACK).")
        return "(" + text[i + 1:end] + ")", end + 1
    start = i
    while i < n and not text[i].isspace():
        i += 1
    return text[start:i], i


def _field_clause(name: str, value: str) -> tuple[str, list]:
    field = FIELDS[name]
    column = REGION_SQL if name == "region" else field.sql

    # A parenthesised list: any of these values for this one field.
    if value.startswith("(") and value.endswith(")"):
        parts = [p for p in re.split(r"\s+(?:OR|or)\s+|\s+", value[1:-1]) if p]
        if not parts:
            raise QueryError(f"Veld '{name}' heeft een lege lijst.")
        subs, params = [], []
        for part in parts:
            sql, vals = _single(name, field, column, part)
            subs.append(sql)
            params.extend(vals)
        return "(" + " OR ".join(subs) + ")", params

    return _single(name, field, column, value)


def _single(name: str, field: Field, column: str, value: str) -> tuple[str, list]:
    if field.kind == _NUM:
        return _numeric(name, column, value)

    if field.kind == _TS:
        return f"{column} >= ?", [value]

    # Text. A trailing star is a prefix search, which is how a visitor asks for
    # "every node whose key starts with these characters".
    if value.endswith("*"):
        stem = value[:-1]
        if not stem:
            raise QueryError(f"Veld '{name}' heeft alleen een sterretje als waarde.")
        return f"{column} LIKE ? ESCAPE '\\'", [_escape_like(stem) + "%"]
    # 'name' and 'path' are haystacks -- several names in one expression, a
    # comma-separated hop list -- so an exact match on the whole column would
    # never hit. They match on containment; the rest match exactly.
    if name in ("name", "path"):
        return f"{column} LIKE ? ESCAPE '\\'", ["%" + _escape_like(value) + "%"]
    return f"{column} = ? COLLATE NOCASE", [value]


def _numeric(name: str, column: str, value: str) -> tuple[str, list]:
    match = _RANGE.match(value)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if low > high:
            raise QueryError(f"Bereik voor '{name}' loopt achteruit: {value}.")
        return f"({column} >= ? AND {column} <= ?)", [low, high]

    for prefix, op in _COMPARISONS:
        if value.startswith(prefix):
            return f"{column} {op} ?", [_number(name, value[len(prefix):])]
    return f"{column} = ?", [_number(name, value)]


def _number(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise QueryError(
            f"Veld '{name}' is een getal, en '{value}' is dat niet.") from None


def _free_text(value: str) -> tuple[str, list]:
    """A bare word: containment across the identifying columns."""
    if value.startswith("(") and value.endswith(")"):
        raise QueryError("Haakjes horen bij een veld, zoals type:(ADVERT OR ACK).")
    like = "%" + _escape_like(value.rstrip("*")) + "%"
    subs = [f"COALESCE({col}, '') LIKE ? ESCAPE '\\'" for col in FREE_TEXT_FIELDS]
    return "(" + " OR ".join(subs) + ")", [like] * len(FREE_TEXT_FIELDS)


def _escape_like(value: str) -> str:
    """Neutralise LIKE's own wildcards inside a value.

    Without this a visitor searching for a literal underscore -- which every
    node name is full of -- would silently get a single-character wildcard, and
    the result would look like a working search returning slightly wrong rows.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def describe_fields() -> list[dict]:
    """The field table, for the help panel on the search page."""
    return [{"name": name, "label": f.label, "kind": f.kind, "hint": f.hint,
             "facet": f.facet}
            for name, f in FIELDS.items()]
