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

Sorting
-------
``parse_sort`` is the second half of the same job: which rows match is one
question, in what order they are shown is another. It deliberately does not live
in the query string. A sort is not a filter -- it changes nothing about the
result set, only about the page of it you are looking at -- and folding it into
the text box would mean a clause that silently does something else than every
other clause, plus a parser that has to keep a "sort:" out of a NOT and out of an
OR list. It is a parameter of its own, with its own small table of columns.

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
                 facet: bool = False, sort: bool = False):
        self.sql = sql          # SQL expression, from this module only
        self.kind = kind        # _TEXT, _NUM or _TS
        self.label = label      # heading in the field list
        self.hint = hint        # example value, shown in the help
        self.facet = facet      # worth offering a "top values" breakdown
        self.sort = sort        # the result list may be ordered by this column


# The joins these expressions assume are in db.search_packets; keep the two in
# step. Sender and observer each get a name of their own as well as a key, since
# a visitor knows nodes by one or the other depending on which they saw last.
#
# ``sort`` marks the fields the result list may be ordered by. It is not simply
# "everything": 'name' and 'path' are haystacks -- a concatenation of two names,
# a comma-separated hop list -- and their alphabetical order means nothing to
# anybody, so they are searchable but never sortable. The reverse case exists
# too: 'path' is worth showing as a column while being useless as an order, and
# COLUMNS below says so separately.
FIELDS: dict[str, Field] = {
    "type":     Field("p.payload_name", _TEXT, "Payloadtype", "ADVERT", facet=True,
                      sort=True),
    "route":    Field("p.route", _TEXT, "Routetype", "FLOOD", facet=True, sort=True),
    "scope":    Field("p.scope", _TEXT, "Bereik", "scoped", facet=True, sort=True),
    "region":   Field("p.scope_region", _NUM, "Regio", "7", facet=True, sort=True),
    "sender":   Field("p.sender", _TEXT, "Afzender (sleutel)", "2ae7c1", facet=True,
                      sort=True),
    "observer": Field("p.observer", _TEXT, "Waarnemer (sleutel)", "2ae7c1d40f93",
                      facet=True, sort=True),
    # The 1-byte destination hash, exactly as the frame carried it. Searchable
    # for the same reason it is worth a column: "what was aimed at this node"
    # is a question the archive could not answer at all before, and the hash is
    # the only thing the wire says about it. One byte names nobody by itself --
    # the API resolves it against the contacts table, with the same honesty
    # about ambiguity a path hop gets -- but the search matches the stored byte,
    # because that is the part that is a fact.
    "dest":     Field("p.dest_hash", _TEXT, "Bestemming (hash)", "c3", sort=True),
    # The 1-byte source hash, exactly as the frame carried it. 'sender' holds
    # the full key an ADVERT stated, which most packets simply do not have;
    # this is the byte the rest of them carry. Two fields rather than one,
    # because they answer different questions -- "packets from this node"
    # against "packets from whoever this byte is" -- and a search that quietly
    # widened the first into the second would return rows nobody asked for. It
    # earns its place the moment the list can print the byte: a sender we
    # cannot name is still the same sender in every packet it sends, and this
    # is how you ask for the rest of them. One byte names nobody by itself, so
    # the API resolves it against the contacts table with all the honesty that
    # needs -- but the search matches the stored byte, the part that is a fact.
    "src":      Field("p.src_hash", _TEXT, "Afzender (hash)", "e3", sort=True),
    "name":     Field("COALESCE(c.name, '') || ' ' || COALESCE(o.name, '')", _TEXT,
                      "Naam van afzender of waarnemer", "BE-HSS"),
    "country":  Field("COALESCE(c.country, o.country)", _TEXT, "Land", "BE", facet=True,
                      sort=True),
    "snr":      Field("p.snr", _NUM, "SNR", ">5", sort=True),
    "rssi":     Field("p.rssi", _NUM, "RSSI", "<-100", sort=True),
    "len":      Field("p.len", _NUM, "Lengte in bytes", "20..40", sort=True),
    "hops":     Field("p.path_len", _NUM, "Aantal hops", ">3", facet=True, sort=True),
    "path":     Field("p.path", _TEXT, "Hop in het pad", "2ae7"),
    "hash":     Field("p.phash", _TEXT, "Payloadhash", "", sort=True),
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


class SortKey:
    """One column the result list may be ordered by."""

    def __init__(self, sql: str, kind: str, nullable: bool = True):
        self.sql = sql              # SQL expression, from this module only
        self.kind = kind            # _TEXT, _NUM or _TS; the page picks a first
        self.nullable = nullable    # ... click direction from it


# The sortable columns, derived from FIELDS so the two can never drift apart: a
# field that is renamed or dropped in the query language takes its sort key with
# it, rather than leaving a key that names a column nobody searches on any more.
#
# Time is the exception that is added by hand. It is not in FIELDS because the
# archive filters on time through the window picker rather than through the query
# language, but it is the column the list is ordered by by default, so it has to
# be sortable -- and it is the one column the schema declares NOT NULL, which the
# ORDER BY below uses.
SORTS: dict[str, SortKey] = {"time": SortKey("p.ts", _TS, nullable=False)}
SORTS.update({
    # 'region' is the one field whose ``sql`` is a placeholder: _field_clause
    # swaps it for REGION_SQL, because the region is stored inside scope_codes
    # rather than in a column. Ordering has to make the same swap, or the query
    # would name a column the packets table does not have.
    name: SortKey(REGION_SQL if name == "region" else f.sql, f.kind)
    for name, f in FIELDS.items() if f.sort
})

DEFAULT_SORT = "time"

# The columns the archive table can show, in the order it shows them in. An
# ordered tuple rather than another flag on Field, because this expresses
# something the field table cannot: where a column sits. Every name in it is a
# key of SORTS or FIELDS -- there is no separate vocabulary for columns -- but
# the two lists are not the same list, and neither is a subset of the other:
# 'path' is worth a column and useless as an order, 'name' is worth searching
# and is already visible inside the sender column, so it is neither.
COLUMNS = ("time", "sender", "src", "dest", "observer", "type", "route", "scope",
           "region", "snr", "rssi", "hops", "len", "path", "hash", "country")

# What the archive shows to somebody who has never chosen anything. Exactly the
# columns it showed before the choice existed, so the page a visitor knows does
# not rearrange itself under them the day this shipped. The rest are one click
# away in the column picker.
DEFAULT_COLUMNS = ("time", "sender", "type", "scope", "snr", "rssi", "hops",
                   "len", "country")

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


class Sort:
    """A validated ordering: which column, which direction, and its ORDER BY.

    ``sql`` is assembled here rather than by the caller so that every character
    of it comes out of this module's own tables. Nothing a visitor typed reaches
    it: the key is looked up in SORTS, and a key that is not in there raises
    instead of being interpolated. That is the whole defence against injection
    through the sort parameter, and it is the reason the column is never passed
    as a string from the API layer -- a parameter placeholder cannot be used for
    a column name, so the only safe alternative to a fixed table would be an
    escaping routine that has to be right every time.
    """

    def __init__(self, key: str, descending: bool):
        column = SORTS[key].sql
        direction = "DESC" if descending else "ASC"
        parts = []
        # Rows whose value is missing go last in *both* directions. SQLite sorts
        # NULL first when ascending, so "sort by SNR, smallest first" would open
        # on a full page of dashes -- the packets whose signal we never recorded,
        # presented as if they were the weakest ones. Written as "x IS NULL"
        # rather than the NULLS LAST clause because that clause needs SQLite
        # 3.30, and this expression works on every version and costs the same.
        if SORTS[key].nullable:
            parts.append(f"{column} IS NULL")
        parts.append(f"{column} {direction}")
        # A tiebreaker that is unique, so the order is total. Without it two
        # packets with the same hop count could swap places between the request
        # for page 1 and the request for page 2, and a row would then appear
        # twice, or not at all, for no reason the reader could see. The id runs
        # with the sort direction so that equal values still read chronologically.
        parts.append(f"p.id {direction}")

        self.key = key
        self.descending = descending
        self.sql = ", ".join(parts)
        # How this ordering is spelled in a URL, so the page and the API agree on
        # one form and a shared link comes back with the order it was shared in.
        self.token = f"{key}:{'desc' if descending else 'asc'}"


def parse_sort(text: str) -> Sort:
    """Turn a ``field`` or ``field:asc|desc`` parameter into a Sort.

    An empty parameter is the archive's own default, newest first. A parameter
    that is not empty but not understood is an error rather than a silent
    fallback to that default: a link that promises "sorted by hops" and quietly
    shows something else is the same class of lie as a search that drops half
    its clauses.
    """
    text = (text or "").strip()
    if not text:
        return Sort(DEFAULT_SORT, True)

    key, _, direction = text.partition(":")
    key, direction = key.strip().lower(), direction.strip().lower()
    if key not in SORTS:
        known = ", ".join(sorted(SORTS))
        raise QueryError(
            f"Sorteren op '{key}' kan niet. Wel mogelijk: {known}.")
    if direction not in ("", "asc", "desc"):
        raise QueryError(
            f"Sorteerrichting '{direction}' bestaat niet; kies asc of desc.")
    # No direction means descending, the same way the archive's default order is
    # newest first: the interesting end of a number of hops, a signal strength or
    # a moment in time is nearly always the top one.
    return Sort(key, direction != "asc")


def describe_fields() -> list[dict]:
    """The field table, for the help panel on the search page."""
    return [{"name": name, "label": f.label, "kind": f.kind, "hint": f.hint,
             "facet": f.facet}
            for name, f in FIELDS.items()]


def describe_sorts() -> list[dict]:
    """The sortable columns, for the page that draws the clickable headings.

    The page gates every heading on this list instead of on a copy of its own,
    for the same reason the filter buttons are gated on describe_fields(): a
    heading that offers an ordering the server refuses is a button that produces
    an error message, and it would appear the moment somebody edits this table.
    """
    return [{"name": name, "kind": s.kind} for name, s in SORTS.items()]


def describe_columns() -> list[dict]:
    """The columns the archive may show, for the picker and the table itself.

    ``sort`` travels along so the page can give a heading its clickable button
    without keeping a second opinion about which columns are sortable; ``default``
    marks the ones shown to a visitor who has never chosen. Order is the order of
    COLUMNS, and the page renders the chosen columns in it rather than in the
    order they were ticked: the table then looks the same whichever route
    somebody took to it, and a shared link cannot arrive with the timestamp in
    the middle. Rearranging columns by hand was considered and left out -- it is
    a second, heavier feature (drag targets, a stored order, a URL that carries
    it) on top of the one that was asked for.
    """
    return [{"name": name, "sort": name in SORTS,
             "default": name in DEFAULT_COLUMNS}
            for name in COLUMNS]
