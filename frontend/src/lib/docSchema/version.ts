// Which schema a build speaks. A client and the collaboration service must
// speak the same one: an editor parses the shared document with its own schema
// and drops what it cannot represent, and y-prosemirror writes that drop back
// to the document as a deletion — so an older page open on a document that
// holds newer nodes or marks erases them for everyone.
//
// Bump DOC_SCHEMA_VERSION whenever the schema changes: a node, a mark, or an
// attribute added, removed or renamed (./extensions.ts and what it builds on).
// The service then refuses connections from builds that speak another one, and
// those pages ask to be refreshed.

export const DOC_SCHEMA_VERSION = 7

/** The query parameter on the WebSocket URL that carries the client's version. */
export const DOC_SCHEMA_PARAM = 'schema'

/** Why the service refused a connection whose schema is not its own. */
export const DOC_SCHEMA_MISMATCH = 'schema-mismatch'
