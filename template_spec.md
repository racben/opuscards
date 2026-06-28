# Anki card template redesign — brief

Self-contained spec for redesigning an Anki **card template** (the HTML/CSS/JS that
renders a note) from scratch. You have no other context from where this came; everything
needed is here. Deliverables are three files for one note type: `front.html`, `back.html`,
`styling.css`. (Attach the current versions to that conversation if you want them as a
starting point — but the visual direction is being replaced, so treat them as reference for
the *behavior* below, not the look.)

## What this card is

An advanced (C1+) Chinese vocabulary card for an immersion learner. Front shows a target
word; back shows its reading, a dictionary-clean definition, the example sentence it was
found in (with the word highlighted), and optional notes. The learner reviews mostly **on a
phone** (AnkiMobile / AnkiDroid), sometimes desktop. The old design's weakness to fix: the
definition and the example sentence didn't read as cleanly separated zones.

## Note type and fields

Note type name: **Chinese Nova**. Fields (all may be referenced in templates):

| Field | Always present? | Content | Notes for rendering |
|---|---|---|---|
| `Expression` | yes | the target word/phrase/grammar pattern | the card's headword; shown on front |
| `Reading` | usually | pinyin with tone marks | may be empty for non-pronounceable targets |
| `Register` | often empty | style/domain tag: 文 书 口 方 古 贬 褒 敬 谦, or 医/法/佛…, combinable with `·` | **render subtly** — small, muted, e.g. `〈文〉`; never as prominent as the definition |
| `Sentence` | often empty | example sentence, clean text, original charset (may be Traditional or Simplified) | empty ⇒ "context-less" card; the sentence zone should vanish entirely |
| `Definition` | yes | dictionary-style meaning of the word itself | the primary back content |
| `Notes` | often empty | a contextual / corrective note | secondary, de-emphasised; hide when empty |
| `Source` | often empty | provenance, e.g. a book or game name | small muted footer; hide when empty |
| `Hint` | usually empty | optional front-side nudge (e.g. part of speech) | shown beside the Expression on the front when present |

There is a sibling note type **Chinese Sentences** (sentence on the front instead of the
word, no `Hint`) sharing the same field names; you may be asked to produce a matching
template for it, but design `Chinese Nova` first.

Two whole **card states** must both look intentional:
1. **Full** — Expression, Reading, Register, Sentence, Definition, (maybe Notes/Source).
2. **Context-less** — Expression, Reading, Register, Definition only; no Sentence, no Source.
   Nothing should look broken or leave a dangling divider when these are absent.

## Anki templating mechanics (constraints — read before coding)

- Templates are Mustache-ish. `{{Field}}` inserts a field. `{{#Field}}…{{/Field}}` renders
  the block only when the field is non-empty — **use this for every optional field** so empty
  ones disappear. `{{FrontSide}}` in `back.html` injects the rendered front. `{{Tags}}`
  inserts the note's space-separated tags. Field names are case-sensitive.
- `back.html` conventionally starts with `{{FrontSide}}` then a divider, so the answer side
  shows the question plus the reveal.
- **No `localStorage` / `sessionStorage` / IndexedDB** — they're unavailable/blocked in Anki
  webviews. Keep all state in the DOM. Inline `<script>` runs on each card render; keep it
  defensive (wrap in an IIFE, null-check elements).
- The card renders in a stripped webview: **no build step, no external JS frameworks**, no
  bundler. Vanilla JS + CSS only. Web fonts may not load on mobile/offline — rely on a
  CJK-capable system font stack, don't depend on a downloaded font.
- Mobile webviews vary (iOS WKWebView, Android WebView). Avoid bleeding-edge CSS; test-grade
  features (flexbox, clamp(), prefers-color-scheme, CSS variables) are fine.
- Must work **offline** (the dictionary links below open externally, that's fine; rendering
  itself must need no network).

## Behaviors to preserve (these are load-bearing — keep them, restyle freely)

1. **Dynamic highlight of the word inside the sentence.** The `Sentence` field is stored
   *clean* (no markup) so it stays searchable/reusable; the template bolds the `Expression`
   occurrence at render time via JS. Reference implementation:
   ```js
   // sentence is in #sentenceMount; expression text is in a hidden #expression
   var vocab = (document.getElementById('expression').textContent || '').trim();
   var mount = document.getElementById('sentenceMount');
   if (vocab && mount) {
     var esc = vocab.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
     mount.innerHTML = mount.innerHTML.replace(new RegExp(esc, 'g'), '<b>$&</b>');
   }
   ```
   A literal match that finds nothing (e.g. a discontinuous grammar pattern) just leaves the
   sentence un-bolded — acceptable, don't over-engineer it. Keep the field clean; do not move
   to storing `<b>` in the field.

2. **Dictionary links.** The Expression links to a dictionary, platform-aware: on mobile,
   a Pleco URL scheme `plecoapi://x-callback-url/s?q=<word>`; on desktop, an MDBG URL
   `https://www.mdbg.net/chinese/dictionary?page=worddict&wdrst=0&wdqb=<word>`. The whole
   sentence also links to MDBG for a full-sentence lookup. UA sniff for mobile:
   `/android|iphone|ipad|ipod/i.test(navigator.userAgent)`. Links should be visually
   invisible (inherit color, no underline) so the card doesn't look like a web page.

3. **Register, rendered subtly** (new). Show `Register` near the reading but clearly
   secondary — a muted chip / parenthetical like `〈文〉`. It must never compete with the
   Definition for attention.

4. **Discreet metadata.** `Source` as a small muted footer (when present). `{{Tags}}` shown
   very discreetly at the very bottom (tiny, low-opacity) — informational, not decorative.

## Theming & sizing requirements

- **Mobile-first**, but legible on desktop too. Fluid type (e.g. `clamp()`); the Chinese
  characters in Expression/Sentence are the visual focus and should be large and crisp.
- **Light and dark mode** via `prefers-color-scheme`, driven by CSS custom properties.
- CJK-first font stack, e.g. `-apple-system, "PingFang SC", "Noto Sans SC", "Heiti SC",
  sans-serif`. Mind Traditional vs Simplified: the same stack must render both well (the
  Sentence/Expression may be either).
- Antialiasing on; comfortable line-height for mixed Han + pinyin.

## Visual design goals (the part to actually design)

- **Cleanly separate the definition zone from the example-sentence zone** — distinct visual
  treatment so they never blur together (this was the main failing of the prior design).
- Calm, readable, "a place to study," not busy. The Expression is the hero on the front;
  on the back the Definition is primary, Sentence supporting, Notes/Register/Source clearly
  tertiary.
- Make the highlighted word in the sentence pop without garishness.
- Graceful empty states (context-less card must look deliberate).
- The learner will describe the **specific new aesthetic direction** in that conversation —
  honor it over any implied style here; this brief fixes structure and behavior, not taste.

## Deliverables

- `front.html` — Expression (linked, large, centered-ish), optional `{{#Hint}}` beside it.
- `back.html` — `{{FrontSide}}`, then Reading + Register, a divider, the Sentence (with the
  highlight + link JS and the hidden `#expression` mount), the Definition zone, then
  `{{#Notes}}`, `{{#Source}}`, and a discreet `{{Tags}}` line. All optional blocks wrapped in
  `{{#Field}}…{{/Field}}`.
- `styling.css` — the `.card` styles, CSS-variable light/dark themes, and the zone styling.

Keep CSS/JS inline-free where Anki expects (CSS in `styling.css`, JS inline in `back.html`).