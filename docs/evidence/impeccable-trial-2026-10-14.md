# impeccable frontend check trial, 2026-10-14

The trial ran impeccable's 59 deterministic frontend checks over `frontend/`, picked out
the rules that report real problems, and wired those into CI. Its file-scanning mode is not adopted; what it found
is now two rendered-contrast ratchets. Every number below is from the trial run against
`frontend/` on 2026-10-14; the count of open sites is a snapshot of that date.

| | |
| --- | ---: |
| Rules in the tool | 59 |
| Rules the file-scanning path can reach | about a dozen |
| Hits across `frontend/` | 46 |
| Real problems in product source | 0 |

## Three channels reach three different sets

The same tree walked three ways does not produce the same rule set, so "46 hits" needs
the channel attached to it.

| Channel | Reads | Rules it can report | How it judges |
| --- | --- | --- | --- |
| Regex | `.vue` `.ts` `.css` | about a dozen | CSS text features only; no layout |
| Static HTML | `.html` | about a dozen | infers from elements; no real layout |
| Browser render | a URL | all 59 | starts the app and a browser |

A synthetic page carrying every triggering pattern reported 17 rules as `.html` and 5 as
`.vue`. `frontend/` is almost entirely `.vue`, so the file-scanning path can reach only
about a dozen of the 59 to begin with.

## What the file scan reported

| Rule | Hits | Where | Judgement |
| --- | ---: | --- | --- |
| low-contrast | 18 | all in `topic-entry-proto.html` | real, but that file is a design demo |
| cramped-padding | 10 | all in `topic-entry-proto.html` | false positives |
| broken-image | 9 | 2 in a comment, 7 in test strings | all false positives |
| side-tab | 7 | 7 product files | deliberate |
| marquee | 1 | `index.html` splash skeleton | false positive |
| gradient-text | 1 | `ChecklistMessage.vue` | deliberate |

The other 53 rules had no hit. `topic-entry-proto.html` describes itself at the top as a
hand-written design demo that does not go through vite; it is not product code and
nothing references it. The `broken-image` hits never reference a broken image — the rule
is regex-only and cannot read context. `cramped-padding` fired on outer containers
(`app`, `phone__screen`, `dialog-stage`) that are not supposed to have padding. The
seven `side-tab` hits are the left accent borders the design system uses for error
panels, quote blocks and admin banners; the one `gradient-text` is the in-progress
shimmer on `ChecklistMessage.vue`.

## What the rendered walk found

Both real findings are contrast, and both sit where a source scan cannot see: an
inherited value, an alpha composite, or a glyph painted by `::before`.

| Site | Read | Floor | Token | Paid off by |
| --- | ---: | ---: | --- | --- |
| white label on the amber solid button | 2.65:1 | 3:1 (glyph) | `--accent` `#f57f17` | #3762 |
| `--faint` carrying text | 3.37–4.33:1 | 4.5:1 | `--faint` `#747a82` | #3691 |

axe-core's `color-contrast` rule never looks at `::before`/`::after` content and skips
nodes moved out of the accessibility tree. The amber button's chevron and the numbered
list markers on the landing page are painted by `::before`, and the landing page's demo
room is `inert` — both findings sit in those blind spots, so axe passes while the
defects stay. That is why the checks below measure the painted result themselves
instead of vendoring an engine.

## What was adopted

- **#3691** `fix(design): text people read on the public pages leaves --faint for --muted`
  — the token is not deepened; readable text moves up a rung. `--faint` stays the
  decoration tone, which is the direction #1328 set when it stopped that rung at 3:1.
- **#3701** `test(a11y): a rendered contrast ratchet over the public pages` — the rule
  and a measure-only baseline. #3728 registered it in the collector as `fe-contrast`
  once the walk was shared between the collector and the e2e gate.
- **#3762** `fix(frontend): put dark ink on the amber CTA labels on the door pages` —
  the seven amber runs leave the frozen debt: `frontend/contrast-baseline.json` 28 → 21,
  nothing added, nothing loosened.

The two ratchets follow the shape the other frontend gates already have: a rule module,
a baseline that may only shrink, and an update mode that only lowers
(`docs/design-system.md` §7).

## Not adopted, and why

- **The file-scanning mode.** Its only rule with signal on this tree is `low-contrast`,
  and all 18 of those hits are in a design demo. Taking it means a 20 MB third-party
  binary in CI plus `broken-image`-class false positives going red repeatedly.
- **axe-core.** It would be a weaker duplicate engine — see the blind spots above.

## Left open on 2026-10-14

- The product's own Vuetify solid primary buttons (153 sites across 119 files) still
  take the white label. Changing the brand fill or the product-wide label is the
  owner's call; both counts were reported to him and no change was made.
- The 295 `--faint` text sites split 214 readable / 81 decorative (18 of those disabled
  and exempt). Only the sites this line touched were moved; a tree-wide sweep is a
  separate, batched decision.
