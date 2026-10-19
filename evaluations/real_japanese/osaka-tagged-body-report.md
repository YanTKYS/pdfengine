# Osaka tagged body — INVESTIGATION / REFUSED

Read-only inspection on 2026-10-19, Linux, from
`613d04feb41327b70a79cba74144237a784a73bc` (merged PR #76).
Source: `evaluations/realpdf/corpus/word_osaka_guideline.pdf`, page 1.
SHA-256 before and after inspection:
`9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`.

**Decision: stop before implementation.** The first selected body paragraph is
entirely covered by per-owner `/ActualText`. Changing its Japanese wording
while preserving those values would leave obsolete replacement text. Original
text equality proves the current mapping, not correspondence after an edit.
No changed-text semantic correspondence or owner allocation contract was
established. This is the requested semantic refusal, not evidence that every
ActualText-bearing document is uneditable.

## Actual hierarchy and visual text

Inspection used the existing `observe_marked_content`, `ContentPage` and pypdf
readers. All 97 observed page scopes are balanced, unnested, tree-backed and
have unique MCIDs. Page object is `3 0 R`, `/StructParents 0`.
`StructTreeRoot 25 0 R` has `/K [29 0 R]`; `29 0 R` is `/Document`.
ParentTree `27 0 R` has `/Nums [0 32 0 R 1 93 0 R 2 105 0 R 3 117 0 R]`;
the page namespace uses the array at `32 0 R`. Other keys were not adopted.

The previously selected visual lines correspond to two complete semantic
paragraphs, not one MCID per paragraph:

```text
StructTreeRoot 25 -> Document 29
  child index 12: P55 -> Span56 MCID23, Span57 MCID24  (foreign greeting)
  child index 13: P58 -> Span59 MCID25, Span60 MCID26, Span61 MCID27,
                        Span62 MCID28, Span63 MCID29, Span64 MCID30
  child index 14: P65 -> Span66 MCID31, Span67 MCID32
  child index 15: P68 -> Span69 MCID33                 (foreign blank)
  child index 16: P70 -> Span71 MCID34                 (foreign blank)
  child index 17: P72 -> attachment-heading spans     (foreign)
```

All references above have generation zero. For the eight selected scopes,
each ParentTree entry names its listed owner; each owner `/K` addresses exactly
its MCID. Owner 66 has scalar `/K 31`; the other seven have singleton arrays.
Their `/Pg` is `3 0 R`. Every `/P` parent link has exactly one matching `/K`
child backlink up to the root. P58 has precisely the six ordered references
59–64; P65 has precisely `[66 0 R 67 0 R]`. Source scope order agrees with
those child orders. No selected owner also holds foreign text.

The table gives decoded content-stream text and decoded owner ActualText
separately. `SPACE` denotes one U+0020, not omitted content.

| Line | MCID / owner | Content text | Owner `/ActualText` |
| --- | --- | --- | --- |
| p1-l14 | 25 / 59 | 標記につ | 標記につ |
| p1-l14 | 26 / 60 | きまして | きまして |
| p1-l14 | 27 / 61 | 、 | 、 |
| p1-l14 | 28 / 62 | 国立感染症研究所においてガイドラインが作成されましたので、参考に送 | 国立感染症研究所においてガイドラインが作成されましたので、参考に送 |
| p1-l15 | 29 / 63 | 付いたします。 | 付いたします。 |
| p1-l15 | 30 / 64 | SPACE | SPACE |
| p1-l16 | 31 / 66 | つきましては、内容について御了知の上、貴会員に周知いただきますようお願いいたします。 | **absent** |
| p1-l16 | 32 / 67 | SPACE | SPACE |

P58 contains 50 characters, with baselines 346.129974 and 364.129974 pt;
P65 contains 43 characters at 382.129974 pt. The first physical line crosses
four distinct Span owners; the second crosses two. MCID31 contains two text
objects within one envelope and one physical line. BDC tags are `/P` for
25–29 and 31, `/Span` for 30 and 32; **BDC `/P` is not owner role `/P`**.
The latter two property dictionaries also contain `/Lang (en-US)`.

Current read-only assertions checked the 97 unique/tree-backed MCIDs, all
selected backlinks, singleton owner content items, one observed source-order
ID per decoded selected character, and exact ActualText equality wherever it
exists. Interpreter errors: zero. The earlier qualification separately proved
93/93 selected native-code matches unique; that font/code audit was not rerun
or generalized to edited output here. An initial diagnostic assertion assumed
all eight owners had ActualText and failed on owner66; the corrected audit
explicitly distinguishes its absence and passed. No PDF was written by either.

## Foreign boundaries and clipping

Selected envelopes occupy merged decoded program byte ranges `[5103,6211)`
(P58) and `[6213,6849)` (P65), including BDC/EMC wrappers. Between envelopes
are whitespace gaps. The preceding foreign greeting is MCIDs23/24 under P55;
its last EMC ends at byte 5101. The following foreign blank MCID33 starts at
6851. Neither shares a selected leaf or paragraph owner. No Form invocation
or nested marked scope occurs in the selected envelopes. All selected text
was accounted for; no foreign glyph is inside those eight envelopes.

Each selected scope includes clipping operators as well as text. The clip is:

```text
q
0.000008871 0 595.32 841.92 re
W* n
BT ... ET
Q
```

MCID31 repeats this sequence twice within its single BDC/EMC pair. This is a
page-sized rectangular clip, not evidence of extra painted content. It is
nevertheless outside `marked_content.paragraph_structure`'s allowed operator
set (`re`, `W*`, `n` are excluded). `source_ownership.context` already admits
proven rectangular clips, but this does not authorize removing the separate
structural-envelope check. Any future support must bind the current clip and
prove island placement and retained state in every group.

## Minimum-pattern assessment and stop reason

The candidate pattern was a complete semantic P with ordered leaf Span
children, preserving every owner, MCID, property and backlink. Common ancestry
alone is insufficient: every edited fragment needs a persistent assignment to
its original owner, and logical order must survive wrapping and follower motion.
Existing B2 puts all new text in the first MCID and empties same-owner siblings.
Applying that policy to P58 would transfer text between six distinct owners,
while leaving their ActualText values behind. It cannot be reused here.

All 50 characters of the requested first body are covered. Retaining all
ActualText values permits no demonstrated natural length-changing wording edit
of that paragraph. Updating leaf ActualText is **not inherently reconstruction
of StructureTree or ParentTree**; it would instead require a new, explicit
semantic rewrite and fragment-allocation contract with current-revision proofs.
No such contract exists in the inspected B2 writer, ownership witness or B1
proposal. Equality to the original glyph text does not supply that authority.
No ActualText is deleted, ignored, or rewritten in this investigation.

Owner66 is an important narrower alternative: its 42 Japanese characters are
not covered by ActualText on the owner or ancestors. Owner67's ActualText is
only its separate trailing space. An owner66-only future edit could in principle
protect owner67 unchanged. This is not a reason to reject unrelated ActualText
on the whole page. However, it changes the selected operation: P58 would remain
foreign, and within this bounded body there is no following substantive body
paragraph after P65 to demonstrate wrap plus related-body push-down. The next
two paragraph siblings are blanks, followed by the attachment heading. Adopting
those as editable body or silently reassigning the protected space is not proved.
This alternative also needs the separate Span-leaf, partial-P, split-line sibling
and clipped-envelope proofs described above. It was assessed read-only and was
not substituted for the requested two-body-paragraph lifecycle.

Thus no single safe pattern for **this selected first-body change plus follower
movement** was established. This is a bounded design decision, not a claim that
owner66 or all real Word PDFs can never be supported. No additional PDF, body
selection or structure pattern was tried.

## Changes, checks and unhandled work

Only this investigation report and the pre-existing qualification report are
committed. The latter is preserved byte-for-byte (SHA-256
`408a85e4786819123c95185ca2785de83be0786015bd53a10a463e3afdf593d7`).
No production code, test fixture, font, PDF, sidecar, StructureTree or ParentTree
changed; no guard was loosened. `docs/acrobat-critical-path.md` remains unchanged
because the boundary is unresolved. Existing environment dependencies worked;
no environment configuration change was needed.

Read-only assertions above passed in approximately 0.31 seconds for the compact
final audit. They are not pytest counts. No focused fixture suite or full suite
was run, because implementation stopped at the required structural preflight.
The earlier B1 refusal (`paragraph needs one tree-backed owner`, followed by
different marked graphics state) remains the recorded production behavior in
[the preserved qualification report](osaka-body-qualification-report.md).

Successful real saves: **0**. Acceptance, changed-text replacement, wrapping,
follower movement, save/reopen/re-edit, saved no-op, output glyph/code/GID/font
audit and MuPDF/Poppler raster comparisons: **not run**. In particular no zero
outside-region or whole-page pixel result is claimed. Source SHA remained exact.
The three-page evaluation was not rerun. Investigation is committed/pushed for
review without a PR or merge; implementation remains stopped.
