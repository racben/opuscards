# opus — Chinese SRS card pipeline

A small CLI toolchain: capture a word while immersing, mine the sentence you saw it
in from your own corpus, generate a concise card back with GPT, and add it to Anki —
tagged for review. No server, no webapp, no credentials in the cloud.

```
capture (Keep / clipboard)
        │
        ▼
   opusmine.py ──► resolve each line: mine from the index, or pass a literal sentence
        │          emits  note_type ⏎ target ⏎ sentence ⏎ source ⏎ instruction  (TSV)
        ▼
   opuscards.py ─► GPT → {expression, reading, definition, notes}; add to Anki (AnkiConnect)
        │
        ▼
      Anki  (cards tagged `chatgpt` + `marked`; QA happens in your next review)

   corpus.py ────► builds the search index the miner reads (run when you add texts)
```

`opus` is a one-touch wrapper over `opusmine | opuscards --anki`.

## Setup (once)

1. **Anki**: install the AnkiConnect add-on; keep Anki open when adding cards.
2. **Note types**:
   - Create **`Chinese Nova`** (vocab, word-front) with fields
     `Expression · Reading · Sentence · Definition · Notes · Source · Hint`.
     Paste in `front.html`, `back.html`, `styling.css`.
   - **`Chinese Sentences`** (sentence-front) already exists; it needs
     `Sentence · Expression · Reading · Definition · Notes` and optionally `Source`.
     No `Hint` needed — the script skips fields a note type doesn't have.
3. **Env**: `export OPENAI_API_KEY=...` (optionally `OPENAI_MODEL`, default `gpt-5.5`).
4. **Index**: `python3 corpus.py build` normalises `~/Chinese Text Analysis` into a
   `_index/` folder **beside the scripts** (kept out of the corpus so cloud-sync clients
   don't try to sync thousands of generated files). `opusmine` and `corpus` both resolve
   it from their own location, so keep them in the same directory.
5. **Alias** (optional): `alias om=opus`. If you instead symlink onto your PATH, the
   `_index/`, `card_prompt_zh.md`, and the scripts all resolve next to the *real* files
   (symlinks are followed), so keep them together there.

## Daily use

```bash
# build / refresh the index after adding sources
python3 corpus.py add ~/Downloads/newnovel.txt   # copy into corpus + index it
python3 corpus.py build                           # rebuild everything

# make cards
pbpaste | uv run opusmine.py | uv run opuscards.py --anki
opus                                              # …same thing, from the clipboard
opus --file ~/keep_inbox.txt                      # …from a synced plaintext file
```

Copy lines out of Google Keep and run `opus`. Keep has no clean local-file/API hook,
so clipboard is the path; if you ever capture into something that syncs a `.txt`
(Obsidian, Drafts, a Drive file), use `opus --file <path>` instead — already wired.

## Capture grammar

One line per card. The target/anchor split is on the **first space or Tab** (so phone
capture with spaces is fine). A trailing `#…` becomes a model instruction (never shown
on the card).

| you type | result |
|---|---|
| `word` | vocab card; mine a sentence for `word` |
| `word anchor` | vocab card; mine the shortest line with `word` **and** `anchor` |
| `word a full sentence。` | vocab card; use that literal sentence (explicit target) |
| `a full sentence。` | vocab card; literal sentence, model picks the target |
| `>…` (prefix) | same, but a **sentence** card (front = sentence) |
| `word` with no corpus hit | context-less card, empty sentence (Pleco shortlist) |
| `… #explain the whole sentence` | passes that instruction to the model |

"Looks like a sentence" = ≥12 chars or contains 。！？…；  (so a short two-word line is
read as word+anchor, a long/punctuated one as a literal sentence).

## Keeping the corpus clean

The corpus can be a junk drawer. `corpus.py` ignores any folder whose name is in
`IGNORE_DIRS` (top of the file) — drop old/raw/unwanted text in such a folder and the
index never sees it. `build --exclude STR` is the ad-hoc version for one-offs.

Game dumps (`AnimeGameData`, `TurnBasedGameData`) are meant to live *outside* the
corpus, as the `DUMPS` fallback `opusmine` greps directly. If a dump has leaked into the
corpus and exploded the index into thousands of files, either move it out and rebuild, or
add its folder name to `IGNORE_DIRS`.

`corpus.py add <dir> --merge NAME` collapses a multi-file source (a game dump, a chapter
folder) into a single `NAME.txt` corpus file indexed as one source. **If the merged
directory lives inside the corpus, also add it to `IGNORE_DIRS`** — otherwise `build`
will re-index the raw originals per-file alongside the merged copy.

## Charset

Captured text is searched as-found. The index keeps each source's **original**
charset; trad/simp variants are generated for the **query** only, so a traditional
source yields a traditional card. (Needs the `opencc` CLI; if it's missing, search
quietly falls back to same-charset matching.)

## Tunables

- `opusmine.py`: `SENTENCE_LIKE` (capture word-vs-sentence cutoff), `KEEP_WHOLE`
  (max length kept un-trimmed), `TINY` (pull-in-neighbour threshold), `DUMPS`
  (game-dump paths → display names used for `Source`). Mined hits are ranked
  shortest-first; drop the `candidates.sort` for first-match-wins.
- `opuscards.py`: `NOTE_TYPES`, `BASE_TAGS`, `--deck`, `--model`. `NO_COLOR=1`
  disables ANSI colour.
- `corpus.py`: cleaning regexes at the top, each commented. The one load-bearing
  rule: any line with no CJK is dropped (kills sprite/image ids).

## Files

| file | what |
|---|---|
| `opusmine.py` | capture → target/sentence/source resolver (mine or passthrough) |
| `opuscards.py` | GPT generation + AnkiConnect, pretty CLI review |
| `corpus.py` | `add` (ingest a file) / `build` (rebuild index) |
| `opus` | one-touch clipboard/file → cards wrapper |
| `card_prompt_zh.md` | the generation prompt (JSON out); `OPUS_PROMPT` to relocate |
| `front.html` `back.html` `styling.css` | the `Chinese Nova` card template |
