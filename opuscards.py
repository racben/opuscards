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
    sentence -> Chinese Sentences   (sentence on the front, target expression
                bolded by the model; sentence_prompt.md)

The script supplies Sentence/Source itself. For sentence cards the model returns the
sentence with <b></b> around the target; the code verifies that stripping the tags
gives back the exact input sentence and falls back to the clean one on mismatch.

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
import re
import sys
from pathlib import Path

import requests

# --- config (env-overridable) ------------------------------------------------
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")          # generation model
ANKI_URL = os.environ.get("ANKI_CONNECT_URL", "http://localhost:8765")  # AnkiConnect endpoint

# Capture type -> Anki note type, prompt file, and the JSON keys the model must return.
# "front" (sentence cards only) is the input sentence with the target bolded by the
# model — that's the one field the model may echo the sentence into, and it's verified
# against the input before use. Prompts load lazily, so a missing sentence prompt only
# matters once a sentence card actually comes through.
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
        "keys": ("front", "expression", "reading", "register", "definition", "notes"),
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


# "front" is the one field the model may echo the sentence into; its bolding is
# accepted only if removing the tags gives back the input sentence unchanged.
_BOLD_TAG = re.compile(r"</?b>")


# --- pretty CLI --------------------------------------------------------------
def emphasize(s: str) -> str:
    """Render stored <b></b> as terminal bold-yellow; without color the raw tags
    stay visible, which shows exactly what the Sentence field will hold."""
    return s.replace("<b>", "\033[1;33m").replace("</b>", "\033[0m") if USE_COLOR else s


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
        out.append(f"{bar} {emphasize(sentence)}")
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
            "     OPUS_PROMPT, OPUS_SENTENCE_PROMPT, NO_COLOR\n"
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
                d.update(expression=target or "测试", reading="cèshì", register="文",
                         definition="离线预览解释。")
                if "front" in d:
                    d["front"] = sentence.replace(target, f"<b>{target}</b>", 1) if target else sentence
            else:
                d = generate(client, args.model, cfg, sentence, target, instruction)

            expr, reading, register = d["expression"], d["reading"], d["register"]
            definition, notes = d["definition"], d["notes"]
            footer_lines = []
            if "front" in d:
                if _BOLD_TAG.sub("", d["front"]) == sentence:
                    sentence = d["front"]
                else:
                    footer_lines.append(c("33", "└ ⚠ bold check failed — kept the unbolded sentence"))

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