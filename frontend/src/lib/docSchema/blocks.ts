// The blocks a document has beyond plain Markdown: callouts, status tags,
// timelines, stat cards, columns, charts, folded sections, formulas,
// footnotes and videos (./video.ts).
//
// Each one is written in a syntax a reader can follow when the Markdown is
// opened anywhere else (GitHub alerts, `:::` containers, `<details>`, `$…$`,
// `[^1]`), and each one is read and written so that what the editor holds and
// what 芝士 reads are the same text. The serializer's output is the canonical
// spelling: the writing guide teaches exactly that spelling, and an edit has to
// quote it byte for byte.
//
// One block, one module named after it: ./callout.ts, ./status.ts,
// ./timeline.ts, ./stats.ts, ./columns.ts, ./chart.ts, ./details.ts, ./math.ts,
// ./footnote.ts and ./video.ts, over the container syntax in ./containers.ts
// and the fields in ./fields.ts. This file holds the list the schema is built
// from (`docBlocks`) and is the surface every importer already uses: each name
// below is re-exported from where it now lives, so no import path outside this
// module changes.
//
// Part of the document schema module (see ./index.ts): nothing here touches
// the DOM or imports the app. The editor adds its own views on top
// (components/panels/doc/blocks/); without them every block still renders as
// plain, readable markup.

import { Callout } from './callout'
import { Chart } from './chart'
import { Column, Columns } from './columns'
import { Details, DetailsContent, DetailsSummary } from './details'
import { field } from './fields'
import { FootnoteDef, FootnoteRef } from './footnote'
import { MathBlock, MathInline } from './math'
import { StatItem, Stats } from './stats'
import { Status } from './status'
import { Timeline, TimelineItem } from './timeline'
import { Video } from './video'

export type { CalloutKind } from './callout'
export { Callout, CALLOUT_KINDS } from './callout'
export type { ChartKind } from './chart'
export { Chart, CHART_HORIZONTAL, CHART_KINDS, chartInfo, chartNumber } from './chart'
export { Column, Columns } from './columns'
export { readContainer } from './containers'
export { Details, DetailsContent, DetailsSummary } from './details'
export { emptyItem, FIELD_NODES } from './fields'
export { FootnoteDef, FootnoteRef } from './footnote'
export { MathBlock, MathInline } from './math'
export { StatItem, Stats } from './stats'
export type { StatusKind } from './status'
export { Status, STATUS_KINDS } from './status'
export { Timeline, TimelineItem } from './timeline'

/** Every node and mark this module adds to the document. */
export const docBlocks = [
  Callout,
  Status,
  Timeline,
  TimelineItem,
  field('timelineWhen', 'when'),
  field('timelineTitle', 'title'),
  field('timelineBody', 'body'),
  Stats,
  StatItem,
  field('statName', 'name'),
  field('statValue', 'value'),
  field('statDelta', 'delta'),
  Columns,
  Column,
  Chart,
  Details,
  DetailsSummary,
  DetailsContent,
  MathInline,
  MathBlock,
  FootnoteRef,
  FootnoteDef,
  Video,
]
