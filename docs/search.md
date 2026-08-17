# The search language

*[Nederlands](nl/search.md)*

The query bar on `/pakketten` speaks a small Kibana-style language.
`server/app/search.py` is the whole of it: there is no second, hidden set of
rules elsewhere, and every character of SQL it produces comes from that module's
own tables.

## The one promise

> **Nothing is ever silently dropped.**

An unknown field name, a comparison on a text column, a malformed range, a
reversed range, an unclosed quote, an impossible sort: each one is an error the
page shows, never a clause quietly skipped. A search that ignores half of what
you asked for while reporting a confident number of hits is worse than one that
refuses to run.

Errors arrive as a 200 with an `error` string on
`GET /api/v1/packets/search` — see [`api.md`](api.md#get-apiv1packetssearch) for
why that is not a 4xx.

## Forms

| Form | Example | Meaning |
|---|---|---|
| `field:value` | `type:ADVERT` | Exact match, case-insensitive |
| several clauses | `type:ADVERT scope:scoped` | Joined with AND |
| wildcard, starts with | `sender:2ae7*` | Text fields only |
| wildcard, ends with | `name:*circuit` | Text fields only |
| wildcard, contains | `name:*circuit*` | Text fields only |
| comparison | `snr:>5`, `rssi:<=-100` | Numeric fields only |
| range | `len:20..40` | Inclusive at both ends, numeric fields only |
| quotes | `name:"BE-XXX-Example.VIR"` | For a value containing spaces |
| negation | `-type:ACK`, `NOT type:ACK` | Either spelling |
| one field, several values | `type:(ADVERT OR TXT_MSG)` | OR inside one field |
| bare word | `2ae7` | Containment across the identifying columns |

Clauses separated by spaces are joined with **AND**, which is what Kibana does.
**OR exists only inside the parentheses of a single field.** That covers "one of
these types" without turning this into an expression parser whose precedence
rules nobody would remember. Inside the parentheses both `A OR B` and a plain
`A B` are accepted as the same list.

Negation wraps the whole clause: `-type:(ACK OR ADVERT)` becomes
`NOT (type = ACK OR type = ADVERT)`.

### The star

**A star stands for "anything", wherever it sits.** One rule rather than three
separate forms, because a visitor who has learned that a star means "anything"
should not then have to learn where it is allowed to stand:

| Query | LIKE pattern | Asks for |
|---|---|---|
| `sender:2ae7*` | `2ae7%` | starts with |
| `name:*circuit` | `%circuit` | ends with |
| `name:*circuit*` | `%circuit%` | contains |
| `name:BE*VIR` | `BE%VIR` | both parts, in that order |

It works inside an OR list (`type:(*MSG* OR ACK)`) and inside a negation
(`-name:*test*`) like any other value.

**Text fields only.** A containment match on a number says nothing — `snr:*5*`
would be asking for a signal strength with a five somewhere in its decimal
notation — so a star on a numeric field is an error, and the message is about the
star rather than about the value not being a number. `Field.kind` is the whole
rule; there is no second list of field names that could drift away from it.
Numeric fields have comparisons and ranges instead, which is what somebody
reaching for `snr:*5*` actually wanted.

A star does override the containment default of `name` and `path`: without one,
`name:BE-HSS` is already `%BE-HSS%` because the column is a haystack, but
`name:BE-HSS*` anchors at the start. The visitor said where the match belongs,
and that answer wins over the column's default.

**On `name` and `path`, an anchor anchors the haystack, not one name.** `name` is
`c.name || ' ' || o.name`, so `name:BE-HSS*` matches when the *sender's* name
starts that way, and `name:*VIR` matches when the *observer's* name ends that way
— and when the observer has no name at all the expression ends in the separator
space, so nothing ends with `VIR`. `path` behaves the same way around its
comma-separated hop list, where "starts with" means the first hop. Both are
honest readings of "the field starts with this", and both are easy to mistake for
"either name starts with this". `name:*BE-HSS*` is the form that asks the
question people usually mean.

## Fields

Kind decides what a value may look like: `num` accepts comparisons and ranges,
`text` accepts wildcards and quotes. Facet fields can be asked for a "top
values" breakdown; sort fields may order the result list.

| Field | Column | Kind | Label | Example | Facet | Sort |
|---|---|---|---|---|---|---|
| `type` | `p.payload_name` | text | Payload type | `ADVERT` | yes | yes |
| `route` | `p.route` | text | Route type | `FLOOD` | yes | yes |
| `scope` | `p.scope` | text | Scope | `scoped` | yes | yes |
| `region` | *(derived)* | num | Region | `7` | yes | yes |
| `sender` | `p.sender` | text | Sender (key) | `2ae7c1` | yes | yes |
| `observer` | `p.observer` | text | Observer (key) | `2ae7c1d40f93` | yes | yes |
| `dest` | `p.dest_hash` | text | Destination (hash) | `c3` | no | yes |
| `src` | `p.src_hash` | text | Sender (hash) | `e3` | no | yes |
| `name` | sender ‖ observer name | text | Name of sender or observer | `BE-XXX` | no | no |
| `country` | sender or observer country | text | Country | `BE` | yes | yes |
| `snr` | `p.snr` | num | SNR | `>5` | no | yes |
| `rssi` | `p.rssi` | num | RSSI | `<-100` | no | yes |
| `len` | `p.len` | num | Length in bytes | `20..40` | no | yes |
| `hops` | `p.path_len` | num | Number of hops | `>3` | yes | yes |
| `path` | `p.path` | text | Hop in the path | `2ae7` | no | no |
| `hash` | `p.phash` | text | Payload hash | | no | yes |

The joins those expressions assume live in `db._SEARCH_FROM`; keep the two in
step. Those joins read `visible_contacts`, not `contacts`, so `name` and
`country` search the *published* values: a repeater with `show_name = 0` does
not match on its real name and does match on its address hash. Confirming a name
to somebody who already suspected it is still telling them. See
[`privacy.md`](privacy.md).

### The ones that behave differently

**`sender` and `src` are separate fields on purpose.** `sender` holds the full
key prefix an ADVERT stated, which most packets simply do not have; `src` is the
one byte the rest of them carry. They answer different questions — "packets from
this node" against "packets from whoever this byte is" — and a search that
quietly widened the first into the second would return rows the visitor never
asked for. The search matches the stored byte, because that is the part that is
a fact; resolving it to a node is the API's job, with all the honesty that
needs. See [`candidates.md`](candidates.md).

**`dest` is its mirror**, and it answers a question the archive could not answer
at all before: what was aimed at this node.

**`name` and `path` match on containment, not equality.** They are haystacks —
`name` is a concatenation of two names, `path` is a comma-separated hop list —
so an exact match on the whole column would never hit. That is also why neither
is sortable: their alphabetical order means nothing to anybody.

**`region` has no column.** The region a scoped packet names is stored inside
`scope_codes`, so both filtering and sorting derive it with `search.REGION_SQL`:

```sql
CAST(NULLIF(substr(p.scope_codes, instr(p.scope_codes, ',') + 1), '0') AS INTEGER)
```

`FIELDS["region"].sql` is a placeholder (`p.scope_region`) that both
`_field_clause()` and the `SORTS` table swap for that expression. Ordering has
to make the same swap, or the query would name a column the packets table does
not have.

### What a bare word searches

`FREE_TEXT_FIELDS`: `p.sender`, `p.observer`, `p.payload_name`, `p.scope`,
`c.name`, `o.name`, `c.country`, `o.country`. Deliberately the identifying
columns only — adding `snr` would mean typing `5` matched a signal strength,
which is never what somebody means by a loose word in a search box.

A bare word is always containment, so a star on either end adds nothing and is
stripped rather than refused: `Jessa`, `Jessa*` and `*Jessa*` produce the same
pattern. A star in the middle is not a decoration and keeps its meaning —
`BE*VIR` becomes `%BE%VIR%`.

## Sorting

Sorting is a **parameter of its own**, not a clause in the query string. A sort
is not a filter — it changes nothing about the result set, only about the page of
it you are looking at — and folding it into the text box would mean one clause
that silently does something else than every other clause, plus a parser that
has to keep a `sort:` out of a `NOT` and out of an `OR` list.

`sort=field` or `sort=field:asc|desc`. No direction means **descending**, the
same way the archive's default order is newest first: the interesting end of a
hop count, a signal strength or a moment in time is nearly always the top one.
Empty means the default, `time:desc`. Anything else is an error rather than a
silent fallback — a link that promises "sorted by hops" and quietly shows
something else is the same class of lie as a search that drops half its clauses.

`SORTS` is derived from `FIELDS` (every field with `sort=True`) plus one entry
added by hand: `time`, on `p.ts`. Time is not in `FIELDS` because the archive
filters on time through the window picker rather than through the query
language, but it is the default order, so it has to be sortable — and it is the
one column the schema declares `NOT NULL`.

The `ORDER BY` that `search.Sort` builds has three parts:

```sql
<column> IS NULL,          -- only for a nullable column
<column> ASC|DESC,
p.id ASC|DESC
```

**Missing values go last in both directions.** SQLite sorts NULL first when
ascending, so "sort by SNR, smallest first" would otherwise open on a full page
of dashes — the packets whose signal was never recorded, presented as if they
were the weakest ones. Written as `x IS NULL` rather than the `NULLS LAST`
clause, which needs SQLite 3.30 and costs the same.

**The id is a tiebreaker that makes the order total.** Without it two packets
with the same hop count could swap places between the request for page 1 and the
request for page 2, and a row would then appear twice, or not at all, for no
reason the reader could see. It runs with the sort direction so equal values
still read chronologically.

### Why the column table is the defence

`Sort.sql` is assembled inside `search.py` so that every character of it comes
out of this module's own tables. The key is looked up in `SORTS`, and a key that
is not there raises instead of being interpolated. That is the whole defence
against injection through the sort parameter, and it is why the column is never
passed as a string from the API layer: a parameter placeholder cannot stand in
for a column name, so the only safe alternative to a fixed table would be an
escaping routine that has to be right every time. The same rule governs
`db.packet_facets()`, whose `column` argument is looked up in `FIELDS` first.

## Columns

`search.COLUMNS` is the ordered tuple of columns the archive table can show:

```
time, sender, src, dest, observer, type, route, scope, region,
snr, rssi, hops, len, path, hash, country
```

`DEFAULT_COLUMNS` — what a visitor who has never chosen anything sees — is
`time, sender, type, scope, snr, rssi, hops, len, country`: exactly the columns
the page showed before the choice existed, so a page a visitor knows does not
rearrange itself under them.

An ordered tuple rather than another flag on `Field`, because it expresses
something the field table cannot: **where** a column sits. Every name in it is a
key of `SORTS` or `FIELDS` — there is no separate vocabulary for columns — but
the two lists are not the same list and neither is a subset of the other:
`path` is worth a column and useless as an order; `name` is worth searching and
is already visible inside the sender column, so it is neither.

The page renders the chosen columns in `COLUMNS` order rather than the order
they were ticked, so the table looks the same whichever route somebody took to
it and a shared link cannot arrive with the timestamp in the middle.
Rearranging columns by hand was considered and left out: it is a second, heavier
feature (drag targets, a stored order, a URL that carries it) on top of the one
that was asked for.

## The three describe functions

The page never keeps a second opinion about what the server accepts:

| Function | Feeds | Shape |
|---|---|---|
| `describe_fields()` | The help panel and the filter buttons | `{name, label, kind, hint, facet}` |
| `describe_sorts()` | The clickable headings | `{name, kind}` |
| `describe_columns()` | The column picker and the table | `{name, sort, default}` |

A heading that offered an ordering the server refuses would be a button that
produces an error message, and it would appear the moment somebody edits the
table. `kind` travels along so the page can pick a sensible first click
direction.

## Escaping

`_escape_like()` neutralises LIKE's own wildcards inside a value: `\` → `\\`,
`%` → `\%`, `_` → `\_`, and every `LIKE` is written with `ESCAPE '\'`. Without
it a visitor searching for a literal underscore — which every node name is full
of — would silently get a single-character wildcard, and the result would look
like a working search returning slightly wrong rows.

**The visitor's star is translated after that escaping, never before.** The two
use the same mechanism, so the order is what keeps them apart: `_escape_like()`
only ever emits `\`, `%` and `_`, never a star, so nothing it produces can be
read back as a wildcard somebody asked for — and a typed `%` is already
neutralised by the time the stars become `%`. The reverse order would turn a
typed `%` into a wildcard, and would leave `name:*_*` searching for three
arbitrary characters instead of for a literal underscore between two wildcards.
`server/tests/test_search.py` asserts that case against real SQLite, because it
is an agreement with the database rather than with a string.

Everything else is a bound parameter. The only strings interpolated into SQL are
column expressions from `FIELDS`, `SORTS` and `REGION_SQL`.

## Error messages

They are written for the person who typed the query, in Dutch, and name the
problem rather than the parser:

| Situation | Message |
|---|---|
| Unknown field | `Onbekend veld 'foo'. Bekende velden: …` |
| Field without a value | `Veld 'snr' heeft geen waarde.` |
| Bare `-` or `NOT` | `Er staat een min of NOT zonder iets erachter.` |
| Unclosed quote | `Een aanhalingsteken is niet afgesloten.` |
| Unclosed parenthesis | `Een haakje is niet gesloten.` |
| Parentheses without a field | `Haakjes horen bij een veld, zoals type:(ADVERT OR ACK).` |
| Empty list | `Veld 'type' heeft een lege lijst.` |
| Only stars as a value | `Veld 'sender' heeft alleen sterretjes als waarde.` |
| A star on a non-text field | `Een sterretje werkt alleen op tekstvelden, en 'snr' is een getalveld.` |
| Non-numeric value on a numeric field | `Veld 'snr' is een getal, en 'abc' is dat niet.` |
| Reversed range | `Bereik voor 'len' loopt achteruit: 40..20.` |
| Unknown sort key | `Sorteren op 'foo' kan niet. Wel mogelijk: …` |
| Unknown sort direction | `Sorteerrichting 'up' bestaat niet; kies asc of desc.` |

## How the parser is built

`_tokenize()` is hand-written rather than one regex over the whole string:
quotes and the parenthesised OR list both contain spaces, and a single
expression that handles those is write-only. It walks the text once, producing
`(negated, field or None, value)` triples; `_read_value()` reads one value —
quoted, parenthesised, or up to the next space.

`parse()` turns those into a `Query` with an SQL fragment and its parameter list.
An empty query yields an empty fragment, which means "match everything";
`db._search_where()` then bounds it by the time window only.

The module is pure: no I/O, no database handle. Which is what makes
`server/tests/test_search.py` and `test_search_sort.py` able to assert on the
generated SQL directly.

## Related documents

| Question | Document |
|---|---|
| The endpoint that runs these queries | [`api.md`](api.md#get-apiv1packetssearch) |
| The columns being searched | [`database.md`](database.md#packets) |
| What `src` and `dest` resolve to | [`candidates.md`](candidates.md) |
