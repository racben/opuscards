# Chinese Nova — field schema

The note type for **vocab cards** (word on the front). `Chinese Sentences` uses the same
field *names* but renders the Sentence on the front and doesn't need `Hint`, so one
generator feeds both note types — with a different prompt and field semantics per card
type (sentence cards are described at the bottom of this file).

Canonical field order: `Expression · Reading · Register · Sentence · Definition · Notes · Source · Hint`
(order matters only for legacy TSV imports — AnkiConnect addresses fields by name).

| Field | What it holds | Filled by | Example |
|---|---|---|---|
| **Expression** | The target word, phrase, or grammar pattern being learned. Shown on the card front. A user-given target stays in its dictionary form even when the sentence splits or inflects it (造势 stays 造势 for 造足了势头 — the in-sentence form goes to Notes); the exact-surface-form rule (so bolding works) applies when the model picks the target, or for same-word variants like trad/simp. | Model (`expression`) | 投名状 |
| **Reading** | Pinyin of the Expression. Tone marks (not numbers), context-correct for polyphones. May be empty for non-pronounceable targets. | Model (`reading`) | tóumíngzhuàng |
| **Register** | Style / tone / domain label, kept separate so the card can render it subtly: 文 书 口 方 古 贬 褒 敬 谦, or a domain (医 法 佛 军…), combinable with `·`. Empty for neutral everyday words. | Model (`register`) | 文 |
| **Sentence** | The example sentence the word was mined or captured in. Original charset, kept clean (no markup); long paragraph lines are trimmed to the target's sentence at retrieval. Empty = context-less card. | `opusmine` (retrieval) | 世间英雄纷纷递来投名状。 |
| **Definition** | Dictionary-clean lexical meaning of the word **itself** (not its use in this sentence): literal sense, then figurative (比喻…) where that's the point, plus a short source tag for allusions (语本《庄子》 — name only, never the quote). No 【】, no pinyin, no register label, no 也作…/见…条 cross-refs. ≤ ~40 hanzi. | Model (`definition`) | 瞪眼望着背影；比喻远远落后、追赶不上。语本《庄子》。 |
| **Notes** | Contextual / corrective note, written **only** when the sentence diverges from the definition (irony, a sense the definition wouldn't predict, pun, register clash) **or** there's a false-friend / easy-misread / wrong-mental-model trap worth flagging for memory. Never restates the definition or narrates an obvious instantiation. Blank otherwise. | Model (`notes`) | 注意「呛」此处不是「呛到（choke）」，而是言语上的顶撞。 |
| **Source** | Provenance — which book or game the sentence came from. Best-effort; blank when the sentence is self-supplied or no source is known. | `opusmine` (index filename) | 重返未来3.4 |
| **Hint** | Optional front-side nudge shown beside the Expression (e.g. part of speech). Manual — the pipeline never fills it. | You (by hand) | 动词 |

## For the generator

The model produces **only these five** fields and returns them as a JSON object:

```json
{
  "expression": "瞠乎其后",
  "reading": "chēng hū qí hòu",
  "register": "书",
  "definition": "瞪眼望着背影；比喻远远落后、追赶不上。语本《庄子》。",
  "notes": ""
}
```

- `Sentence` and `Source` are supplied by the script, **not** the model — don't echo them.
- `register` is its own field; keep style/domain labels out of `definition`.
- `definition` is the word's own dictionary meaning, not its use in this sentence; that's
  what `notes` is for, and only when the sentence diverges or there's a trap.
- `notes` is `""` when there's nothing worth adding (no divergence, no false-friend).
- If only a target word is given with no sentence (a context-less card), give the word's
  core meaning, reading, and register; `notes` stays `""`.

## Rendering behavior

Empty fields disappear on the card — the template wraps `Sentence`, `Notes`, `Source`,
and `Hint` in conditionals. So a context-less vocab card (empty Sentence, empty Source)
shows just Expression, Reading, and Definition with nothing dangling. The Expression is
bolded inside the Sentence dynamically (the field itself stays clean).

## Chinese Sentences — sentence cards

For expressions, collocations, and non-compositional usages of known words, shown in
their original sentence (front = sentence). Same field names, different semantics
(prompt: `sentence_prompt.md`; spec: `handoff.md`):

| Field | Sentence-card meaning |
|---|---|
| **Sentence** | The original sentence with the target wrapped in `<b></b>` **by the model** — the deliberate exception to the clean-field rule, because bound material (狠狠<b>敲他一笔</b>) and discontinuous spans (<b>敲</b>了他好大<b>一笔</b>) can't be bolded by template matching. `opuscards` accepts the bolding only if stripping the tags reproduces the mined sentence exactly; otherwise it stores the clean sentence and warns. A capture with no sentence is an error, not a context-less card. |
| **Expression** | The target expression itself, untagged (back-side display, duplicate check). |
| **Reading** | Pinyin of the target expression only. |
| **Definition** | The *explanation* (释义): shortest monolingual definition that kills the most likely wrong reading — adaptive length, deliberately allowed to run longer than the vocab deck's ~40-hanzi cap when the nuance is load-bearing. Defines the sense used in this sentence, not all senses. Register is folded in as `〈…〉` (the note type has no Register field). |
| **Notes** | Empty by design. Filled only when a `#` instruction asks for extras (a whole-sentence walkthrough, 近义词, variants). |
| **Source** | Provenance, from `opusmine`, same as vocab. |

Dropped from the handoff spec on purpose: the `Pattern` field (slot structure) and
default near-synonyms — both judged noise for the majority of cards; synonyms are
available per-card via `#`.

## Chinese Vocab — plain cards

For words where a monolingual definition adds nothing — loan words, flora/fauna,
chemicals, transliterations. Deliberately context-less: `opus plain` never mines, and
the note type has no Sentence field (prompt: `plain_prompt.md`):

| Field | Plain-card meaning |
|---|---|
| **Expression** | The target word, returned exactly as given. |
| **Reading** | Pinyin, tone marks, as for vocab cards. |
| **Definition** | A quick **English** gloss — the shortest accurate equivalent (`yarrow`), not a dictionary entry. |
| **Usage** | Extra note only when strictly necessary (rare): disambiguation, domain restriction, register flag (regional/dialect, slang, vulgar, dated), or a flag that the word isn't the everyday term for the thing (with the common word). |

`Image` and `Source` exist on the note type but are left blank by the pipeline.