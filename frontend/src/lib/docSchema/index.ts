// The living document's schema, as one module the editor and anything else
// that has to read or write the same document can share.
//
// ONE extension list defines the document: the editors build on it, the
// round-trip check compares against it, and the test corpus runs through it. If
// two of them used different schemas the check would be meaningless, so they
// cannot: all of them import from here — the collaboration service too.
//
// This module imports nothing from the app — no Vue, no API layer, no `@/`
// alias, no DOM at import time — so code outside the browser bundle can build
// the identical schema. eslint.config.mjs holds it to that.
//
// The live document is a Yjs document in this schema; its Markdown is derived
// (./yjs.ts). Syntax the schema cannot represent does not survive the first
// conversion of a Markdown document, so `compareRoundTrip` says what a
// conversion changed — the collaboration service logs it; the original stays in
// the version history.

export { carryCommentAnchors, COMMENT_ANCHOR, commentAnchors } from './commentAnchors'
export type { DocExtensionsOptions, DocImageOptions } from './extensions'
export { docExtensions } from './extensions'
export type { RoundTripReport } from './fidelity'
export { compareRoundTrip, normalizeMarkdown } from './fidelity'
export { docMarked, finishMarkdown, serializeDoc } from './markdown'
export type { PendingSuggestion } from './suggestions'
export { pendingSuggestions, suggestionAuthor, suggestionId, withoutSuggestions } from './suggestions'
export { DOC_SCHEMA_MISMATCH, DOC_SCHEMA_PARAM, DOC_SCHEMA_VERSION } from './version'
export {
  exportMarkdown,
  FIELD,
  liveNode,
  liveSuggestions,
  nodeMarkdown,
  parseMarkdown,
  readsAs,
  writeMarkdown,
} from './yjs'
