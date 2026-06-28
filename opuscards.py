# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openai>=1.0.0",
#     "requests>=2.31.0",
# ]
# ///
"""opuscards — turn opusmine records into Anki notes, with a pretty CLI review.

Reads TSV from opusmine on stdin:
    note_type <TAB> target <TAB> sentence <TAB> source <TAB> instruction

For each row it asks the model for discrete fields {expression, reading, definition,
notes}, supplies Sentence/Source itself, and builds a note in the right note type:
    vocab    -> Chinese Nova        (word on the front)
    sentence -> Chinese Sentences   (sentence on the front)

Every note is tagged `chatgpt` + `marked`, so new cards surface (starred) in your
next review and you fix-or-unmark inline. A formatted card is printed for every row;
with --anki it also adds the note and shows a ✓ / ⚠ duplicate / ✗ line. Fields the
target note type doesn't have are silently skipped (so Chinese Sentences never
needs a Hint field).

  echo '投名状\t世间英雄纷纷递来投名状。' | opuscards.py            # review only
  pbpaste | opusmine.py | opuscards.py --anki                       # full pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

# --- config (env-overridable) ------------------------------------------------
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")          # generation model
ANKI_URL = os.environ.get("ANKI_CONNECT_URL", "http://localhost:8765")  # AnkiConnect endpoint
PROMPT_PATH = Path(os.environ.get("OPUS_PROMPT", str(Path(__file__).with_name("card_prompt_zh.md"))))
NOTE_TYPES = {"vocab": "Chinese Nova", "sentence": "Chinese Sentences"}  # capture type -> Anki model
BASE_TAGS = ["chatgpt", "marked"]                                  # added to every note

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if (USE_COLOR and s) else s


# --- AnkiConnect -------------------------------------------------------------
def anki(action: str, **params):
    r = requests.post(ANKI_URL, json={"action": action, "version": 6, "params": params}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")


_field_cache: dict[str, set | None] = {}


def model_fields(model_name: str):
    """Cached field-name set for a note type; None if it can't be fetched (don't filter)."""
    if model_name not in _field_cache:
        try:
            _field_cache[model_name] = set(anki("modelFieldNames", modelName=model_name))
        except Exception:
            _field_cache[model_name] = None
    return _field_cache[model_name]


def build_note(deck: str, model_name: str, fields: dict, tags: list[str]) -> dict:
    avail = model_fields(model_name)
    if avail is not None:
        fields = {k: v for k, v in fields.items() if k in avail}
    return {"deckName": deck, "modelName": model_name, "fields": fields,
            "tags": tags, "options": {"allowDuplicate": False}}


# --- generation --------------------------------------------------------------
def generate(client, model: str, system_prompt: str, sentence: str, target: str, instruction: str):
    prompt = f"Input:\n{sentence}\n\nTarget:\n{target}\n\nCustom instruction:\n{instruction}"
    resp = client.responses.create(
        model=model, instructions=system_prompt, input=prompt, text={"verbosity": "low"}
    )
    raw = (resp.output_text or "").strip()
    if raw.startswith("```"):                      # defensive: unwrap a stray code fence
        raw = raw.strip("`")
    if "{" in raw and "}" in raw:                  # tolerate leading/trailing chatter
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)
    return (str(data.get("expression", "")).strip(),
            str(data.get("reading", "")).strip(),
            str(data.get("register", "")).strip(),
            str(data.get("definition", "")).strip(),
            str(data.get("notes", "")).strip())


# --- pretty CLI --------------------------------------------------------------
def render(note_type, expr, reading, register, sentence, definition, notes, source, tags, footer):
    bar = c("2", "│")
    mark = c("2", "[句]" if note_type == "sentence" else "[词]")
    head = f"{mark} {c('1;33', expr)}"
    if reading:
        head += f"  {c('36', reading)}"
    if register:
        head += f"  {c('2', '〈' + register + '〉')}"
    if source:
        head += f"  {c('2', '·' + source)}"
    out = [head]
    if sentence:
        out.append(f"{bar} {sentence}")
    out.append(f"{bar} {definition}")
    if notes:
        out.append(f"{bar} {c('2', '\U0001F4DD ' + notes)}")
    if tags:
        out.append(f"{bar} {c('2', '\U0001F3F7  ' + ' · '.join(tags))}")
    if footer:
        out.append(footer)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="opuscards",
        description="Generate Chinese card fields from opusmine records and (optionally) add them to Anki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "input (TSV from opusmine):\n"
            "  note_type <TAB> target <TAB> sentence <TAB> source <TAB> instruction\n\n"
            "env: OPENAI_API_KEY (required), OPENAI_MODEL, ANKI_CONNECT_URL, OPUS_PROMPT, NO_COLOR\n"
        ),
    )
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model (default: {DEFAULT_MODEL})")
    ap.add_argument("--deck", default="Chinese", help="target Anki deck (default: Chinese)")
    ap.add_argument("--anki", action="store_true", help="add notes via AnkiConnect (else review only)")
    ap.add_argument("--dry-run", action="store_true", help="skip the model; print placeholder fields")
    ap.add_argument("--tag", action="append", default=[], metavar="TAG",
                    help="extra tag on every note (repeatable); chatgpt+marked are always added")
    args = ap.parse_args()

    tags = BASE_TAGS + args.tag

    if args.dry_run:
        client = system_prompt = None
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            ap.error("OPENAI_API_KEY not set (or use --dry-run).")
        if not PROMPT_PATH.exists():
            ap.error(f"prompt not found at {PROMPT_PATH} (set OPUS_PROMPT).")
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        from openai import OpenAI
        client = OpenAI()

    n_total = n_added = n_dup = n_err = 0
    for raw in sys.stdin:
        if not raw.strip():
            continue
        n_total += 1
        cols = raw.rstrip("\n").split("\t")
        cols += [""] * (5 - len(cols))
        note_type, target, sentence, source, instruction = cols[:5]
        model_name = NOTE_TYPES.get(note_type, NOTE_TYPES["vocab"])
        try:
            if args.dry_run:
                expr, reading, register, definition, notes = (target or "测试", "cèshì", "文", "离线预览解释。", "")
            else:
                expr, reading, register, definition, notes = generate(
                    client, args.model, system_prompt, sentence, target, instruction)

            footer = ""
            if args.anki:
                # Route register to its own field if the note type has one; otherwise fold it
                # into the Definition as 〈文〉… so register survives until you add the field.
                avail = model_fields(model_name)
                has_register = bool(avail) and "Register" in avail
                definition_out = definition if (has_register or not register) else f"〈{register}〉{definition}"
                fields = {"Expression": expr, "Reading": reading, "Sentence": sentence,
                          "Definition": definition_out, "Notes": notes, "Source": source, "Hint": ""}
                if has_register:
                    fields["Register"] = register
                try:
                    nid = anki("addNote", note=build_note(args.deck, model_name, fields, tags))
                    footer = c("32", f"\u2514 \u2713 added \u00b7 {nid}")
                    n_added += 1
                except RuntimeError as e:
                    if "duplicate" in str(e).lower():
                        footer = c("33", "\u2514 \u26a0 skipped (duplicate)")
                        n_dup += 1
                    else:
                        footer = c("31", f"\u2514 \u2717 {e}")
                        n_err += 1
            print(render(note_type, expr, reading, register, sentence, definition, notes, source, tags, footer))
            print()
        except Exception as e:
            n_err += 1
            print(c("31", f"\u2717 error on {raw.rstrip()!r}: {e}"), file=sys.stderr)

    # summary
    parts = [f"{n_total} cards"]
    if args.anki:
        parts += [c("32", f"{n_added} added")]
        if n_dup:
            parts.append(c("33", f"{n_dup} duplicate"))
    if n_err:
        parts.append(c("31", f"{n_err} error"))
    print(c("2", "— " + " · ".join(parts) + " —"), file=sys.stderr)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())