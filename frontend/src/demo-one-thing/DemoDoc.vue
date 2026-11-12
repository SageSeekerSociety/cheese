<script setup lang="ts">
// A living document of the project: the product's document editor
// (DocEditor with the doc schema, drawn by DocProse), so 芝士's pending
// suggestion is the editor's own suggestion marks, and 王磊's caret is the
// product's caret. What 林晓 types in the script lands in item 4. A person's
// typing is reported as `update` (a channel's 文档 keeps it; 项目文件 logs it in
// 历史, one entry per stretch).
import type { Node as PMNode } from '@tiptap/pm/model'

import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { avatarColor } from '@/utils/avatar'

import { DocEditor } from '@/components/panels/doc/docEditor'
import DocPresence from '@/components/panels/doc/DocPresence.vue'
import DocProse from '@/components/panels/doc/DocProse.vue'
import { renderCaret } from '@/lib/docCaret'
import { docExtensions } from '@/lib/docSchema'

const props = defineProps<{ html: string; peer?: boolean; typing?: boolean }>()
const emit = defineEmits<{ (e: 'update', html: string): void }>()

const PEER = { handle: 'wanglei', name: '王磊', color: avatarColor('wanglei') }

/** Where a caret goes: the end of the paragraph whose text ends with `tail`. */
function endOf(doc: PMNode, tail: string): number | null {
  let found: number | null = null
  doc.descendants((node, pos) => {
    if (found !== null || node.type.name !== 'paragraph') return
    if (node.textContent.endsWith(tail)) found = pos + 1 + node.content.size
  })
  return found
}

const key = new PluginKey('demo-carets')
let showPeer = !!props.peer
let showMine = !!props.typing

const Carets = Extension.create({
  name: 'demoCarets',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key,
        props: {
          decorations(state) {
            const out: Decoration[] = []
            const peerAt = showPeer ? endOf(state.doc, '截断实验。') : null
            if (peerAt !== null) out.push(Decoration.widget(peerAt, () => renderCaret(PEER), { side: 1 }))
            if (showMine) {
              // Item 4 is the last paragraph of the list: the typed text ends there.
              let last: number | null = null
              state.doc.descendants((node, pos) => {
                if (node.type.name === 'paragraph') last = pos + 1 + node.content.size
              })
              if (last !== null)
                out.push(
                  Decoration.widget(last, () => {
                    const c = document.createElement('span')
                    c.className = 'ot-caret'
                    return c
                  })
                )
            }
            return DecorationSet.create(state.doc, out)
          },
        },
      }),
    ]
  },
})

const editor = shallowRef(
  new DocEditor({
    extensions: [...docExtensions(), Carets],
    editable: true,
    content: props.html,
    editorProps: { attributes: { class: 'doc-prose' } },
  })
)

// Only the editor's own transactions: setContent below is told not to emit.
editor.value.on('update', ({ editor: ed }) => emit('update', ed.getHTML()))

watch(
  () => [props.html, props.peer, props.typing] as const,
  ([html, peer, typing]) => {
    showPeer = !!peer
    showMine = !!typing
    if (editor.value.getHTML() !== html) editor.value.commands.setContent(html, { emitUpdate: false })
    else editor.value.view.dispatch(editor.value.state.tr)
  }
)

onBeforeUnmount(() => editor.value.destroy())
</script>

<template>
  <div class="ot-doc">
    <div v-if="peer" class="ot-doc__presence">
      <DocPresence
        :peers="[
          { clientId: 2, handle: 'wanglei', agent: false, name: '王磊', avatar: '', color: PEER.color },
          { clientId: 3, handle: 'linxiao', agent: false, name: '林晓', avatar: '', color: avatarColor('linxiao') },
        ]"
      />
    </div>
    <DocProse :editor="editor" :loading="false" />
  </div>
</template>

<style scoped>
.ot-doc {
  position: relative;
}

.ot-doc__presence {
  position: absolute;
  top: 0;
  right: 0;
  z-index: var(--z-raised);
}

.ot-doc :deep(.ot-caret) {
  display: inline-block;
  width: 1px;
  height: 1.2em;
  margin-left: 1px;
  vertical-align: text-bottom;
  background: var(--ink);
  animation: ot-blink 1s steps(1) infinite;
}

@keyframes ot-blink {
  50% {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ot-doc :deep(.ot-caret) {
    animation: none;
  }
}
</style>
