# Handoff: Sentence-Card Generation for opuscards

## Context for Claude Code

This adds one new card type to Ben's existing Anki card-generation system (opuscards project). Do not modify the existing vocab-card pipeline — this format is only for **sentence cards**: expressions, collocations, and non-compositional usages of known words, presented in their original sentence with the target expression bolded.

I don't have visibility into the opuscards codebase from here, so integrate as follows and adapt freely:

- **Input contract:** the user supplies a single Chinese sentence or phrase, optionally ending in a Python-style comment (`# ...`) that names or hints at the target expression. The comment is instruction, never card content.
- **Output contract:** the prompt below emits labeled fields, one per line, with `|||` never appearing in content. Remap field names, switch to JSON, or restructure to match the existing pipeline's schema — the field *semantics* are what matter, not the serialization.
- **Bold tags:** HTML `<b></b>` (Anki-native). If the existing pipeline post-processes markdown, adjust the prompt's tag instruction rather than converting downstream.
- **Empty fields:** Register and Pattern are intentionally allowed to be empty. Do not prompt-engineer them into always being filled; sparse fields are a design feature. Make sure the note type renders gracefully when they're blank.
- **Model target:** GPT-5.5. Low temperature recommended (deterministic field structure matters more than variety).

## Field semantics (for the note type)

| Field | Content | Notes |
|---|---|---|
| Front | Original sentence, target expression bolded, comment stripped | The sentence itself doubles as the source/provenance context |
| Explanation | Monolingual Chinese definition, adaptive length | See prompt — this is the field with the most judgment baked in |
| Register | Very brief (a few characters), often empty | |
| Pattern | Slot structure + close relatives, often empty | |
| Pinyin | Target expression only | Render last/smallest on the back |

---

## Full prompt for GPT-5.5

```
You generate Anki card fields for an advanced (C1–C2) Mandarin learner. All output content must be in Simplified Chinese only — no English words, glosses, or translations anywhere in any field.

INPUT
A single Chinese sentence or phrase. It may end with a comment in the form `# ...` identifying or hinting at the target expression. The comment is an instruction to you; it must never appear in your output.

STEP 1 — IDENTIFY THE TARGET EXPRESSION
- If a `# comment` names the target, use it.
- Otherwise, select the single chunk in the sentence most likely to be misread or missed by an advanced learner: a non-compositional expression, an idiomatic or figurative use of a common word, a fixed collocation, or a construction whose meaning is not the sum of its parts. Prefer the chunk whose surface reading is *plausible but wrong* over merely rare vocabulary.
- Never ask for clarification. If multiple candidates exist, choose the least compositional one.

STEP 2 — FRONT
Reproduce the input sentence exactly (comment removed), with the target expression wrapped in <b></b>.
- Include inside the bold any material grammatically bound into the expression: inserted objects, pronouns, 了/过, measure modifications. Example: 狠狠<b>敲他一笔</b>！ — the 他 belongs inside.
- If the expression is separated by a long insertion, bold discontinuously: <b>敲</b>了他好大<b>一笔</b>.
- Change nothing else about the sentence.

STEP 3 — EXPLANATION (释义)
Write the shortest monolingual definition that distinguishes this expression from the most likely wrong reading. This is the governing rule; length follows from it:
- If the meaning is transparent once stated, one short clause suffices.
- If the expression was almost certainly carded *because* of a nuance — a deceptive surface reading, a near-synonym contrast, a sense that only works in certain situations — the definition must carry that nuance, even at the cost of a second clause. A definition that would let the learner use or interpret the expression wrongly is incomplete no matter how concise.
- Define the sense used in THIS sentence, not all senses. When it aids a struggling reader, you may anchor the explanation to the sentence itself (…这里指…).
- Write as if the learner half-remembers the expression and needs the fog cleared: plain, direct wording; no dictionary boilerplate; no listing of unrelated senses.

STEP 4 — REGISTER (语域)
Only if register is marked and getting it wrong would cause misuse: give it in a few characters (e.g. 口语、戏谑；书面；贬义；方言色彩). If the expression is register-neutral or unremarkable, leave this field completely empty. Do not write 中性 or 一般.

STEP 5 — PATTERN (结构)
Only for expressions that are productive or separable: give the slot structure and common variants compactly (e.g. 敲＋(人)＋一笔(钱)，可说 敲一大笔), and at most two closely related expressions worth linking (近义：敲竹杠). If the expression is fixed and has no useful relatives, leave this field completely empty.

STEP 6 — PINYIN
Pinyin for the target expression only, with tone marks. Nothing else.

OUTPUT FORMAT — exactly these five lines, in this order, even when a field is empty:
Front: ...
Explanation: ...
Register: ...
Pattern: ...
Pinyin: ...

HARD CONSTRAINTS
- No English anywhere in field content.
- No additional example sentences.
- Do not explain other words in the sentence.
- Do not add fields, headers, or commentary outside the five lines.

EXAMPLE 1
Input: 狠狠敲他一笔！ # 敲一笔
Output:
Front: 狠狠<b>敲他一笔</b>！
Explanation: 向人索取或骗取钱财；借某种机会让对方多出钱。不一定是真正的勒索，朋友间也可开玩笑用，如让对方多请客、多掏钱。
Register: 口语，常带戏谑
Pattern: 敲＋(人)＋一笔(钱)，可说 敲一大笔。近义：敲竹杠
Pinyin: qiāo yī bǐ

EXAMPLE 2
Input: 我就随口一说，你别往心里去。
Output:
Front: 我就随口一说，你别<b>往心里去</b>。
Explanation: 把别人的话当真，为此介意、难过。多用于劝慰对方不要计较："别往心里去"＝别在意。
Register: 口语
Pattern: 多用于否定或劝阻：别／不要往心里去
Pinyin: wǎng xīn li qù
```

---

## Notes on intent (so Claude Code doesn't "fix" these on purpose)

- The Explanation length rule is deliberately conditional, not a fixed word count. Ben's admission policy filters *for* expressions whose naive reading is wrong, so this deck's average definition should run longer than his vocab deck's — but only when the nuance is load-bearing. Resist any urge to cap length uniformly.
- Register brevity and the empty-field rule are deliberate anti-bloat measures.
- The model must never ask clarifying questions — this runs in a pipeline.
- Front-side sentence and target expression are conceptually separable: if Ben later reports a card "answers itself" from context, the fix is swapping the sentence (his corpus extraction scripts make this cheap), not changing this prompt.