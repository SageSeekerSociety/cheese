// 建议 mode (#3830 revision mode): what a person types becomes a suggestion —
// insertions and deletions marked with their name — instead of a change to the
// text. A mentor's editor is always in it (the service lets nothing else
// through, collab/suggestGuard.ts); a member switches it on in the top bar.
//
// The marks and the commands are @handlewithcare/prosemirror-suggest-changes'.
// What is turned into a suggestion is exactly what its own dispatch decorator
// turns: the reader's own edits. Not an edit that came from another editor
// through the shared document, not an undo or redo, and not accepting or
// rejecting a suggestion (its commands mark those to be skipped).
//
// Text, and a paragraph split or joined, the library records as marks on text;
// a whole block inserted, removed or retyped (paragraphs pasted in, a list item
// added, a paragraph made a heading) as a mark on the block, which the shared
// document keeps too. A block a table, a row of columns, a timeline or stat cards hold
// in a fixed shape cannot carry one (docSchema's suggestableBlocks): the
// library throws when it tries, and that edit is dropped with `onRefused` said
// instead.
import type { Editor } from '@tiptap/core'
import type { Transaction } from '@tiptap/pm/state'

import {
  disableSuggestChanges,
  enableSuggestChanges,
  isSuggestChangesEnabled,
  suggestChanges,
  suggestChangesKey,
  transformToSuggestionTransaction,
} from '@handlewithcare/prosemirror-suggest-changes'
import { Extension } from '@tiptap/core'

import { suggestionId } from './docSchema'

function ownEdit(tr: Transaction): boolean {
  const sync = (tr.getMeta('y-sync$') ?? {}) as { isUndoRedoOperation?: boolean; isChangeOrigin?: boolean }
  return (
    !tr.getMeta('history$') &&
    !tr.getMeta('collab$') &&
    !sync.isUndoRedoOperation &&
    !sync.isChangeOrigin &&
    !('skip' in ((tr.getMeta(suggestChangesKey) as object | undefined) ?? {}))
  )
}

export function createSuggestMode(opts: { author: () => string; onRefused?: () => void }) {
  return Extension.create({
    name: 'suggestMode',
    addProseMirrorPlugins: () => [suggestChanges()],
    dispatchTransaction({ transaction, next }) {
      const state = this.editor.state
      if (!isSuggestChangesEnabled(state) || !ownEdit(transaction)) return next(transaction)
      let suggested: Transaction
      try {
        suggested = transformToSuggestionTransaction(transaction, state, () => suggestionId(opts.author()))
      } catch {
        opts.onRefused?.()
        return
      }
      next(suggested)
    },
  })
}

/** Switch 建议 mode on or off in this editor. */
export function setSuggesting(editor: Editor, on: boolean): void {
  if (isSuggestChangesEnabled(editor.state) === on) return
  ;(on ? enableSuggestChanges : disableSuggestChanges)(editor.state, editor.view.dispatch)
}
