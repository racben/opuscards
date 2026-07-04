#!/usr/bin/env python3
"""opusmine — resolve capture lines into structured records for opuscards.

Reads capture lines (stdin, or --file), parses the line grammar, and for each line
either MINES a sentence from the corpus index or PASSES THROUGH a literal sentence.

Emits TSV (one row per card):
    note_type <TAB> target <TAB> sentence <TAB> source <TAB> instruction

Line grammar (a trailing  " #instruction" — hash at line start or after whitespace —
is stripped first and passed to the model; an inline x#y is left alone):
    word                  vocab card; mine a sentence for `word`
    word <TAB> anchor     vocab card; mine the shortest line containing `word` AND `anchor`
    word <TAB> <sentence> vocab card; literal sentence, explicit target (no mining)
    <sentence>            vocab card; literal sentence, model picks the target
    >...                  any of the above, but a SENTENCE card (front = sentence)
    (no corpus hit)       context-less: empty sentence (e.g. a Pleco shortlist word)

Charset: trad+simp variants are generated for the QUERY only (the cheap side); the
index keeps its original charset, so a card comes out in the charset of its source.
Trimming is length-gated and happens here, at retrieval, where the target is known.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CORPUS = HOME / "Chinese Text Analysis"               # originals (searched by `rch`, not opusmine)
INDEX = Path(__file__).resolve().parent / "_index"    # flat index lives beside the scripts
# Raw game dumps, greped only with -d/--dumps (the curated genshin/hsr corpuses in the
# main corpus dir cover normal use). Mapped to display names because there's no
# quest-level granularity; upgrade the values here later.
DUMPS = {
    HOME / "src" / "AnimeGameData": "Genshin",
    HOME / "src" / "TurnBasedGameData": "Honkai: Star Rail",
}

SENT_PUNCT = "。！？!?…；;"   # characters that end a sentence (used for both detection and trimming)

# A capture token is treated as a LITERAL SENTENCE (passed through, not mined) if it
# is at least this many characters OR contains sentence punctuation. Below it, a bare
# token is a word to mine, and a post-TAB token is a disambiguating anchor. Lower this
# if you capture very short standalone sentences; raise it if you mine long words.
SENTENCE_LIKE = 12

# A mined line this short or shorter is returned WHOLE, never trimmed. Keeps multi-clause
# short lines intact (别碰那个！很危险！ stays whole for a 危险 card). Your stated 30–40 range.
KEEP_WHOLE = 40

# After trimming a long line to the target's sentence, if that sentence is shorter than
# this, the previous sentence is prepended for context. Stops a bare 很危险！ losing its setup.
TINY = 12

# Stop collecting after this many usable candidates, then rank them. Bounds the work
# when a frequent word (esp. with a weak anchor) matches a lot of lines; the cap is
# applied AFTER anchor- and degenerate-filtering, so junk hits never crowd out real ones.
MAX_HITS = 50

# A mined line must beat the target's own length by this margin, or it's discarded as a
# menu entry / item name / title rather than a sentence (a corpus line that IS the word
# would otherwise always win any shortest-style ranking).
MIN_MARGIN = 3

# Among usable candidates, prefer punctuated lines (titles rarely carry 。！？), then the
# length closest to this — compact enough to review, long enough to carry real context.
IDEAL_LEN = 20

# rg skips files larger than this. The curated index is tiny, so this only affects the
# game-dump fallback: it stops a stray huge file from making a fallback crawl drag. Raise
# it if a legitimately large source ever gets silently skipped.
MAX_FILESIZE = "50M"


def variants(text: str) -> list[str]:
    """Original + simplified + traditional. Degrades to [text] if opencc is unavailable,
    so a broken/missing opencc just means same-charset matching rather than a crash."""
    out = [text]
    for cfg in ("t2s.json", "s2t.json"):
        try:
            r = subprocess.run(["opencc", "-c", cfg], input=text, text=True,
                               capture_output=True, check=True)
            v = r.stdout.strip()
            if v and v not in out:
                out.append(v)
        except Exception:
            pass
    return out


def split_sentences(text: str) -> list[str]:
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in SENT_PUNCT:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return [p for p in (s.strip() for s in parts) if p]


def trim(line: str, targets: list[str]) -> str:
    if len(line) <= KEEP_WHOLE:
        return line
    parts = split_sentences(line)
    idxs = [i for i, p in enumerate(parts) if any(t in p for t in targets)]
    if not idxs:
        return line
    i = idxs[0]
    chunk = parts[i]
    if len(chunk) < TINY and i > 0:
        chunk = parts[i - 1] + chunk
    return chunk


# Dump hits are grepped RAW (they never pass through corpus.py's cleaner), so strip the
# structural noise that JSON game-exports carry. This is deliberately minimal — a leading
# "<id>": " wrapper, a trailing ",  and inline tags/ids. Anything beyond this is corpus
# maintenance, not opusmine's job.
_JSON_KEY = re.compile(r'^\s*"[^"]*"\s*:\s*"')   # leading  "12132432020536924126": "
_JSON_TAIL = re.compile(r'"\s*,?\s*$')           # trailing  ",
_LEAD_ID = re.compile(r"^\[.*?\]\s*")            # leading [146782006]
_TAG = re.compile(r"<[^>]+>")                    # <color=#...> </color>


def clean_hit(text: str) -> str:
    text = _JSON_KEY.sub("", text)
    text = _JSON_TAIL.sub("", text)
    text = _LEAD_ID.sub("", text)
    text = _TAG.sub("", text)
    return text.strip()


def rg(targets: list[str], paths: list[Path]) -> list[str]:
    # --null separates path from text with NUL instead of ":", so filenames
    # containing a colon (e.g. a merged "Honkai: Star Rail.txt") can't corrupt the split.
    cmd = ["rg", "--fixed-strings", "--with-filename", "--no-heading", "-N", "--null",
           "--max-filesize", MAX_FILESIZE,
           "-g", "!old/", "-g", "!Anki_dump/"]
    for t in targets:
        cmd += ["-e", t]
    cmd += [str(p) for p in paths]
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.returncode not in (0, 1):
        print(r.stderr, file=sys.stderr, end="")
        return []
    return r.stdout.splitlines()


def source_for(path_str: str) -> str:
    p = Path(path_str)
    if INDEX in p.parents:
        return p.stem
    for root, name in DUMPS.items():
        if root in p.parents:
            return name
    return ""


def mine(target: str, anchor: str, dumps: bool) -> tuple[str, str]:
    tvars = variants(target)
    avars = variants(anchor) if anchor else None
    # Index only by default; -d/--dumps adds the raw game dumps as a fallback tier,
    # greped only when the index misses.
    searches = [[INDEX], list(DUMPS.keys())] if dumps else [[INDEX]]
    for paths in searches:
        paths = [p for p in paths if p.exists()]
        if not paths:
            continue
        candidates = []
        for hit in rg(tvars, paths):
            path_str, _, text = hit.partition("\0")
            text = clean_hit(text)              # strip dump JSON-wrapping / tags / ids
            if not text:
                continue
            if avars and not any(a in text for a in avars):
                continue
            sent = trim(text, tvars)
            if len(sent) < len(target) + MIN_MARGIN:   # title/menu line, not a sentence
                continue
            no_punct = not any(c in sent for c in SENT_PUNCT)
            candidates.append((no_punct, abs(len(sent) - IDEAL_LEN), sent, source_for(path_str)))
            if len(candidates) >= MAX_HITS:        # enough to pick a good one from
                break
        if candidates:
            candidates.sort(key=lambda c: (c[0], c[1]))   # punctuated first, then nearest IDEAL_LEN
            return candidates[0][2], candidates[0][3]
    return "", ""   # context-less


def is_sentence_like(s: str) -> bool:
    return len(s) >= SENTENCE_LIKE or any(c in s for c in SENT_PUNCT)


def parse_line(raw: str):
    # An instruction starts at a "#" that opens the line or follows whitespace, so an
    # inline x#y (a URL fragment, a tag-like token inside a sentence) is left alone
    # instead of truncating the capture.
    m = re.search(r"(?:^|\s)#", raw)
    if m:
        body, instruction = raw[:m.start()].strip(), raw[m.end():].strip()
    else:
        body, instruction = raw.strip(), ""
    if not body:
        return None

    note_type = "vocab"
    if body.startswith(">"):
        note_type = "sentence"
        body = body[1:].strip()

    target = sentence = anchor = ""
    parts = body.split(None, 1)              # split on the first run of whitespace (space OR tab)
    if len(parts) == 2 and not is_sentence_like(parts[0]):
        target = parts[0]
        right = parts[1].strip()
        if is_sentence_like(right):
            sentence = right                 # explicit target + literal sentence
        else:
            anchor = right                   # word + disambiguating anchor
    elif is_sentence_like(body):
        sentence = body                      # literal passthrough, model picks target
    else:
        target = body                        # bare word -> mine

    return note_type, target, sentence, anchor, instruction


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="opusmine",
        description="Resolve capture lines into  note_type<TAB>target<TAB>sentence<TAB>source<TAB>instruction  records.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "capture grammar (a trailing  ' #instruction' — hash after whitespace — is stripped\n"
            " and passed to the model; the target/anchor split is on the first space OR tab):\n"
            "  word                  vocab card; mine a sentence for `word`\n"
            "  word anchor           vocab card; mine the shortest line with `word` AND `anchor`\n"
            "  word a sentence。      vocab card; use that literal sentence (explicit target)\n"
            "  a sentence。           vocab card; literal sentence, model picks the target\n"
            "  >...                  any of the above as a SENTENCE card (front = sentence)\n"
            "  (no corpus hit)       context-less: empty sentence (e.g. a Pleco shortlist word)\n\n"
            "examples:\n"
            "  pbpaste | opusmine.py | opuscards.py --anki\n"
            "  opusmine.py --file ~/keep.txt -d | opuscards.py\n"
        ),
    )
    ap.add_argument("--file", type=Path, help="read captures from a file instead of stdin")
    ap.add_argument("-d", "--dumps", action="store_true",
                    help="also grep the raw game dumps when the index misses (default: index only)")
    args = ap.parse_args()

    lines = args.file.read_text(encoding="utf-8").splitlines() if args.file else sys.stdin
    for raw in lines:
        parsed = parse_line(raw.rstrip("\n"))
        if not parsed:
            continue
        note_type, target, sentence, anchor, instruction = parsed
        source = ""
        if not sentence and target:            # nothing literal given -> mine
            sentence, source = mine(target, anchor, args.dumps)
        # TSV is the wire format: a stray tab inside a field would shift columns
        # downstream, so flatten any to a space here at the single emit point.
        print("\t".join(f.replace("\t", " ") for f in
                        (note_type, target, sentence, source, instruction)))


if __name__ == "__main__":
    main()