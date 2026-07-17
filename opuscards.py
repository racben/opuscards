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

Each capture type has its own prompt, JSON keys, and Anki note type (CARD_TYPES):
    vocab    -> Chinese Nova        (word on the front; card_prompt_zh.md)
    sentence -> Chinese Sentences   (sentence on the front; sentence_prompt.md)
    plain    -> Chinese Vocab       (word on the front, NO sentence; English gloss
                + optional Usage note; plain_prompt.md)

The script supplies Sentence/Source itself; the Sentence field is always stored
clean — the note templates bold the target dynamically (JS), the model never
echoes the sentence.

Every note is tagged `chatgpt` + `marked`, so new cards surface (starred) in your
next review and you fix-or-unmark inline. A formatted card is printed for every row;
with --anki it also adds the note and shows a ✓ / ⚠ duplicate / ✗ line. Fields the
target note type doesn't have are silently skipped (so Chinese Sentences never
needs a Hint field, and its missing Register folds into Definition as 〈…〉).

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

# Capture type -> Anki note type, prompt file, and the JSON keys the model must return.
# Prompts load lazily, so a missing sentence prompt only matters once a sentence card
# actually comes through.
_here = Path(__file__).parent
CARD_TYPES = {
    "vocab": {
        "note_type": "Chinese Nova",
        "prompt": Path(os.environ.get("OPUS_PROMPT", str(_here / "card_prompt_zh.md"))),
        "keys": ("expression", "reading", "register", "definition", "notes"),
    },
    "sentence": {
        "note_type": "Chinese Sentences",
        "prompt": Path(os.environ.get("OPUS_SENTENCE_PROMPT", str(_here / "sentence_prompt.md"))),
        "keys": ("expression", "reading", "register", "definition", "notes"),
    },
    # Context-less by design: no sentence is mined or shown, the definition is a quick
    # English gloss (loan words, flora/fauna, ...), and Usage replaces Notes/Register.
    "plain": {
        "note_type": "Chinese Vocab",
        "prompt": Path(os.environ.get("OPUS_PLAIN_PROMPT", str(_here / "plain_prompt.md"))),
        "keys": ("expression", "reading", "definition", "usage"),
    },
}
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
# Enforced server-side (structured outputs), so the response is guaranteed to be
# exactly the JSON object the card type's keys describe — no fence-stripping or
# brace-hunting needed.
def card_schema(keys) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "string"} for k in keys},
        "required": list(keys),
        "additionalProperties": False,
    }


def system_prompt_for(cfg: dict) -> str:
    """Load a card type's prompt on first use, so a missing sentence prompt only
    matters once a sentence card actually comes through."""
    if "system_prompt" not in cfg:
        if not cfg["prompt"].exists():
            raise RuntimeError(
                f"prompt not found at {cfg['prompt']} (set OPUS_PROMPT / OPUS_SENTENCE_PROMPT)")
        cfg["system_prompt"] = cfg["prompt"].read_text(encoding="utf-8")
    return cfg["system_prompt"]


def generate(client, model: str, cfg: dict, sentence: str, target: str, instruction: str) -> dict:
    prompt = f"Input:\n{sentence}\n\nTarget:\n{target}\n\nCustom instruction:\n{instruction}"
    resp = client.responses.create(
        model=model, instructions=system_prompt_for(cfg), input=prompt,
        text={"verbosity": "low",
              "format": {"type": "json_schema", "name": "card_fields",
                         "schema": card_schema(cfg["keys"]), "strict": True}},
    )
    data = json.loads(resp.output_text)
    return {k: data[k].strip() for k in cfg["keys"]}


# --- pretty CLI --------------------------------------------------------------
def render(note_type, expr, reading, register, sentence, definition, notes, source, tags, footer):
    bar = c("2", "│")
    mark = c("2", {"sentence": "[句]", "plain": "[素]"}.get(note_type, "[词]"))
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
            "env: OPENAI_API_KEY (required), OPENAI_MODEL, ANKI_CONNECT_URL,\n"
            "     OPUS_PROMPT, OPUS_SENTENCE_PROMPT, OPUS_PLAIN_PROMPT, NO_COLOR\n"
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
        client = None
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            ap.error("OPENAI_API_KEY not set (or use --dry-run).")
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
        cfg = CARD_TYPES.get(note_type, CARD_TYPES["vocab"])
        model_name = cfg["note_type"]
        try:
            # A sentence card IS its sentence; a capture that mined nothing has no card.
            if note_type == "sentence" and not sentence:
                raise ValueError("sentence card needs a sentence (no corpus hit?)")
            if args.dry_run:
                d = dict.fromkeys(cfg["keys"], "")
                stub = dict(expression=target or "测试", reading="cèshì", register="文",
                            definition="离线预览解释。")
                d.update({k: v for k, v in stub.items() if k in d})
            else:
                d = generate(client, args.model, cfg, sentence, target, instruction)

            # Not every card type has every key: plain has usage instead of register/notes.
            expr, reading, register = d["expression"], d["reading"], d.get("register", "")
            definition, notes = d["definition"], d.get("notes", "")
            usage = d.get("usage", "")
            footer_lines = []
            if args.anki:
                # Route register to its own field if the note type has one; otherwise fold it
                # into the Definition as 〈文〉… so register survives until you add the field.
                avail = model_fields(model_name)
                has_register = bool(avail) and "Register" in avail
                definition_out = definition if (has_register or not register) else f"〈{register}〉{definition}"
                fields = {"Expression": expr, "Reading": reading, "Sentence": sentence,
                          "Definition": definition_out, "Notes": notes, "Usage": usage,
                          "Source": source, "Hint": ""}
                if has_register:
                    fields["Register"] = register
                try:
                    nid = anki("addNote", note=build_note(args.deck, model_name, fields, tags))
                    footer_lines.append(c("32", f"\u2514 \u2713 added \u00b7 {nid}"))
                    n_added += 1
                except RuntimeError as e:
                    if "duplicate" in str(e).lower():
                        footer_lines.append(c("33", "\u2514 \u26a0 skipped (duplicate)"))
                        n_dup += 1
                    else:
                        footer_lines.append(c("31", f"\u2514 \u2717 {e}"))
                        n_err += 1
            footer = "\n".join(footer_lines)
            print(render(note_type, expr, reading, register, sentence, definition,
                         notes or usage, source, tags, footer))
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