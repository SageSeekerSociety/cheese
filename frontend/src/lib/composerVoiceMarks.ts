// 语音输入在输入框里留的两个记号，只是画，不进正文：
//
// - 识别中：光标处一小段扫光的占位，告诉人字会落在哪儿；
// - 落字后：刚说进来的那一段淡入，垫一层很淡的琥珀底，慢慢退掉。
//
// 两个都按正文位置记，人在这期间接着打字，记号跟着文字走（ProseMirror 的 mapping）。
import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { posAt } from './composerDoc'

interface Marks {
  slot: number | null
  landed: { from: number; to: number } | null
}

/** 改记号时随事务带上：`slot` 是光标处的字符偏移，`landed` 是 [起, 止) 偏移；null 是拿掉。 */
export interface VoiceMarkMeta {
  slot?: number | null
  landed?: [number, number] | null
}

export const voiceMarksKey = new PluginKey<Marks>('composerVoiceMarks')

function slotWidget(): HTMLElement {
  const el = document.createElement('span')
  el.className = 'composer-voice-slot'
  el.setAttribute('aria-hidden', 'true')
  return el
}

function decorations(doc: PMNode, marks: Marks): DecorationSet {
  const decos: Decoration[] = []
  if (marks.slot !== null) decos.push(Decoration.widget(marks.slot, slotWidget, { side: 1, key: 'voice-slot' }))
  if (marks.landed && marks.landed.to > marks.landed.from)
    decos.push(Decoration.inline(marks.landed.from, marks.landed.to, { class: 'composer-voice-landed' }))
  return DecorationSet.create(doc, decos)
}

export function createVoiceMarks(): Extension {
  return TiptapExtension.create({
    name: 'composerVoiceMarks',
    addProseMirrorPlugins() {
      return [
        new Plugin<Marks>({
          key: voiceMarksKey,
          state: {
            init: () => ({ slot: null, landed: null }),
            apply(tr, old) {
              let next: Marks = old
              if (tr.docChanged) {
                next = {
                  slot: old.slot === null ? null : tr.mapping.map(old.slot, 1),
                  landed: old.landed && {
                    from: tr.mapping.map(old.landed.from, 1),
                    to: tr.mapping.map(old.landed.to, -1),
                  },
                }
              }
              const meta = tr.getMeta(voiceMarksKey) as VoiceMarkMeta | undefined
              if (!meta) return next
              return {
                slot: meta.slot === undefined ? next.slot : meta.slot === null ? null : posAt(tr.doc, meta.slot),
                landed:
                  meta.landed === undefined
                    ? next.landed
                    : meta.landed && { from: posAt(tr.doc, meta.landed[0]), to: posAt(tr.doc, meta.landed[1]) },
              }
            },
          },
          props: {
            decorations(state) {
              const marks = this.getState(state)
              return marks ? decorations(state.doc, marks) : null
            },
          },
        }),
      ]
    },
  })
}
