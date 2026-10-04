// A living document that lives only in this page: the same Yjs document an
// editor binds to, with nobody else connected. The preview site shows the doc
// panel with one; tests build the documents they edit with one.

import type { HocuspocusProvider } from '@hocuspocus/provider'
import type { DocSession } from '../composables/useDocCollab'

import { Awareness } from 'y-protocols/awareness'
import * as Y from 'yjs'

import { writeMarkdown } from './docSchema'

export function localDocSession(markdown = '', document = 'local', doc = new Y.Doc()): DocSession {
  if (markdown) writeMarkdown(doc, markdown)
  // The carets need an awareness to read and write; nothing else of a provider
  // is touched by the editor.
  const provider = { awareness: new Awareness(doc) } as unknown as HocuspocusProvider
  return { document, doc, provider, user: { name: '', color: '', avatar: '' } }
}
