// The living document's schema, as one module the editor and anything else
// that has to read or write the same document can share.
//
// ONE extension list defines the document: the editors build on it, the
// round-trip check compares against it, and the test corpus runs through it. If
// two of them used different schemas the check would be meaningless, so they
// cannot: all of them import from here.
//
// This module imports nothing from the app — no Vue, no API layer, no `@/`
// alias, no DOM at import time — so code outside the browser bundle can build
// the identical schema. eslint.config.mjs holds it to that.
//
// 军规 1 (never silently drop content): the document is Markdown and the
// panel reads and writes it whole. Syntax the visual editor cannot represent
// would be destroyed by a load→save cycle, so `compareRoundTrip` detects that at
// load time and the panel pauses autosave and shows a banner. The escape hatch
// is source mode, which edits the raw Markdown and cannot be lossy.

export type { DocExtensionsOptions, DocImageOptions } from './extensions'
export { docExtensions } from './extensions'
export type { RoundTripReport } from './fidelity'
export { compareRoundTrip, normalizeMarkdown } from './fidelity'
export { docMarked, finishMarkdown, serializeDoc } from './markdown'
export { exportMarkdown, FIELD, liveNode, parseMarkdown, readsAs, writeMarkdown } from './yjs'
