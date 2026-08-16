#!/usr/bin/env python3
"""Read the MeshStatsNet version and its changelog entry out of the source.

The release workflow needs three things that already exist in exactly one
authoritative place each, and copying them anywhere else is how they start
disagreeing:

  * the version   -- MESHSTATS_VERSION in MeshStatsNet.h
  * the notes     -- the block for that version in the changelog comment at the
                     top of MeshStatsNet.cpp
  * the agreement -- that the git tag being built names the same version

That last one is the reason this is a script and not three lines of shell. A tag
'fw-v1.12.0' on a tree whose header still says 1.11.0 produces a release whose
assets report a different version than the release does, and the site would then
offer an upgrade that installs something else. Better to fail the build.

Usage:
    release_notes.py version                 -> 1.12.0
    release_notes.py notes [--version X]     -> the changelog block, as markdown
    release_notes.py check --tag fw-v1.12.0  -> exit 0 when they agree

Deliberately no dependencies: it runs in CI before anything is installed, and on
a laptop with nothing but a Python.
"""

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "examples" / "simple_repeater"
HEADER = SRC / "MeshStatsNet.h"
BODY = SRC / "MeshStatsNet.cpp"

# ' * 1.12.0 An upgrade path that ...' starts a block; the next such line ends it.
ENTRY = re.compile(r"^ \* (\d+\.\d+\.\d+)\s+(.*)$")
CONT = re.compile(r"^ \*(?:        )?(.*)$")


def version() -> str:
    text = HEADER.read_text(encoding="utf-8")
    m = re.search(r'#define\s+MESHSTATS_VERSION\s+"([^"]+)"', text)
    if not m:
        sys.exit(f"MESHSTATS_VERSION not found in {HEADER}")
    return m.group(1)


def notes(want: str) -> str:
    """The changelog block for `want`, with the comment scaffolding removed.

    The block is prose in fixed-width comment form: a first line beginning with
    the version, continuation lines indented eight spaces under it. Emitting
    that as one blob is technically faithful and unreadable -- the 1.12.0 entry
    alone is some four hundred words -- so the paragraphs have to be recovered.

    They are recoverable without guessing, because of how this file is written:
    a new thought always starts on a fresh source line ("Why ...", "Rejected:
    ...", "And the thing this may never do ..."). So a sentence that ends
    exactly at the end of a source line is a paragraph break, and a sentence
    that ends anywhere else is not. Inside a wrapped paragraph the two coincide
    only by accident, and when they do the cost is one extra break at a sentence
    boundary -- which reads fine.

    No attempt is made to invent bullets or headings on top of that. The text
    was written as paragraphs and reads as paragraphs; anything more would be
    guessing at structure the author did not put there.
    """
    lines = BODY.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    collecting = False
    for line in lines:
        m = ENTRY.match(line)
        if m:
            if collecting:
                break                      # the next version starts here
            if m.group(1) != want:
                continue
            collecting = True
            out.append(m.group(2).rstrip())
            continue
        if not collecting:
            continue
        if line.strip() in ("*/", "* /"):
            break
        c = CONT.match(line)
        if c is None:
            break
        out.append(c.group(1).rstrip())

    if not out:
        sys.exit(f"no changelog block for {want} in {BODY}")

    paragraphs: list[str] = []
    buf: list[str] = []
    for line in out:
        text = line.strip()
        if not text:
            continue
        buf.append(text)
        if text.endswith((".", ":", "?", ".)")):
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("version", "notes", "check"))
    ap.add_argument("--version", default=None)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    if args.action == "version":
        print(version())
        return

    if args.action == "notes":
        print(notes(args.version or version()))
        return

    if not args.tag:
        sys.exit("check needs --tag")
    tag = args.tag.rsplit("/", 1)[-1]
    want = tag[len("fw-"):] if tag.startswith("fw-") else tag
    want = want[1:] if want.startswith("v") else want
    have = version()
    if want != have:
        sys.exit(
            f"tag {tag} says {want} but MESHSTATS_VERSION says {have}. "
            "Bump the header, or tag the version that is in it -- a release "
            "whose assets report a different version than the release does "
            "sends the site looking for an upgrade that installs something else."
        )
    print(have)


if __name__ == "__main__":
    main()
