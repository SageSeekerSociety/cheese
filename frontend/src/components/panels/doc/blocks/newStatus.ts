// A new status tag from the slash menu: choose the kind, type the words, Enter.
//
// Typing the words straight into the text does not work: a tag ends where its
// words end, so the first letter typed after an empty one would land outside
// it. The words are asked for first and inserted as one tagged run.
import type { Editor } from '@tiptap/core'
import type { StatusKind } from '../../../../lib/docSchema/blocks'

import { STATUS_KINDS } from '../../../../lib/docSchema/blocks'
import { insertStatus } from '../../../../lib/docStatus'

import { closePopover, popoverAt } from './popover'

import { t } from '@/i18n'

export function newStatusAt(editor: Editor): void {
  const at = editor.state.selection.from
  let kind: StatusKind = 'ok'
  const el = document.createElement('div')
  const kinds = document.createElement('div')
  kinds.className = 'doc-pop__kinds'
  const input = document.createElement('input')
  input.type = 'text'
  input.maxLength = 40
  input.setAttribute('aria-label', t('work.room.doc.blocks.statusWords'))
  input.placeholder = t('work.room.doc.blocks.statusWords')
  const buttons = (Object.keys(STATUS_KINDS) as StatusKind[]).map((k) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'doc-menu__item'
    button.textContent = `${STATUS_KINDS[k]} ${t(`work.room.doc.blocks.statusKinds.${k}`)}`
    button.setAttribute('aria-pressed', String(k === kind))
    button.addEventListener('click', () => {
      kind = k
      buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(Object.keys(STATUS_KINDS)[i] === k)))
      input.focus()
    })
    return button
  })
  kinds.append(...buttons)
  el.append(kinds, input)
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return
    e.preventDefault()
    const words = input.value
    closePopover()
    if (words.trim()) insertStatus(editor, at, kind, words)
    else editor.commands.focus()
  })
  const caret = editor.view.coordsAtPos(at)
  popoverAt(new DOMRect(caret.left, caret.top, 0, caret.bottom - caret.top), el, () => {
    if (!editor.isFocused) editor.commands.focus()
  })
  input.focus()
}
