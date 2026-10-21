// 语音输入在输入框里留的两个记号，只是画，不进正文：
//
// - 录着、整理中：光标处一枚小药丸（「正在听」「正在整理」），告诉人字会落在哪儿；
// - 落字后：刚说进来的那一段从头逐字显出来（`revealed` 是显到第几个字，前沿四个字半透明），
//   垫一层琥珀浅底；显完了浅底慢慢退掉。
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
  slotLabel: string
  slotThinking: boolean
  landed: { from: number; to: number } | null
  revealed: number | null
}

/**
 * 改记号时随事务带上：`slot` 是光标处的字符偏移，`landed` 是 [起, 止) 偏移；null 是拿掉。
 * `slotLabel` / `slotThinking` 是药丸上写的字、是不是整理中；`revealed` 是落下的那段
 * 显到了第几个字，null 是全显出来了。
 */
export interface VoiceMarkMeta {
  slot?: number | null
  slotLabel?: string
  slotThinking?: boolean
  landed?: [number, number] | null
  revealed?: number | null
}

/** 落字时前沿那几个半透明的字。 */
const TAIL = 4

export const voiceMarksKey = new PluginKey<Marks>('composerVoiceMarks')

function slotWidget(label: string, thinking: boolean): () => HTMLElement {
  return () => {
    const el = document.createElement('span')
    el.className = thinking ? 'composer-voice-slot composer-voice-slot--thinking' : 'composer-voice-slot'
    el.setAttribute('aria-hidden', 'true')
    const dot = document.createElement('i')
    const text = document.createElement('span')
    text.textContent = label
    el.append(dot, text)
    return el
  }
}

function decorations(doc: PMNode, marks: Marks): DecorationSet {
  const decos: Decoration[] = []
  if (marks.slot !== null)
    decos.push(
      Decoration.widget(marks.slot, slotWidget(marks.slotLabel, marks.slotThinking), {
        side: 1,
        key: `voice-slot-${marks.slotThinking ? 'thinking' : 'listening'}-${marks.slotLabel}`,
      })
    )
  const landed = marks.landed
  if (landed && landed.to > landed.from) {
    if (marks.revealed === null)
      decos.push(Decoration.inline(landed.from, landed.to, { class: 'composer-voice-landed' }))
    else {
      const shown = Math.min(landed.to, landed.from + marks.revealed)
      const tail = Math.min(landed.to, shown + TAIL)
      if (shown > landed.from) decos.push(Decoration.inline(landed.from, shown, { class: 'composer-voice-revealing' }))
      if (tail > shown)
        decos.push(Decoration.inline(shown, tail, { class: 'composer-voice-revealing composer-voice-tail' }))
      if (landed.to > tail) decos.push(Decoration.inline(tail, landed.to, { class: 'composer-voice-pending' }))
    }
  }
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
            init: () => ({ slot: null, slotLabel: '', slotThinking: false, landed: null, revealed: null }),
            apply(tr, old) {
              let next: Marks = old
              if (tr.docChanged) {
                next = {
                  ...old,
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
                slotLabel: meta.slotLabel ?? next.slotLabel,
                slotThinking: meta.slotThinking ?? (meta.slot !== undefined ? false : next.slotThinking),
                revealed:
                  meta.revealed === undefined ? (meta.landed !== undefined ? null : next.revealed) : meta.revealed,
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
