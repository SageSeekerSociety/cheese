<script setup lang="ts">
// 打字的那一块：发消息的输入区和改消息的框都用它。
//
// 正文是一段字符串（v-model），就是要发出去的那段：`<@zhangsan>` 原样在里面，画出来是
// 一枚标签，和文档里、对话里同一个样子（lib/tokenChips.ts）。标签当一个字走：退格整枚
// 删掉，方向键一步跳过。所以草稿怎么存、叫没叫芝士、发什么，外面照旧只认这段字符串。
//
// 光标在正文里的位置也是 v-model（`caret`，按字符数）：@ 补全只看光标前面那一截，挑中
// 一项之后由外面改正文、再把光标放回去。
//
// 按键先交给外面（`keydown`）：回车是发送、挑人还是换行，只有输入区知道；外面
// preventDefault 了，这里就不再处理。粘贴也一样先交出去（截图、长文字）；外面没认领，
// 这里只收纯文字——从网页贴进来的格式不进正文。
import type { RefNames } from '../../lib/refChip'

import { onBeforeUnmount, onMounted, ref, shallowRef, watch, watchEffect } from 'vue'
import { Editor, Extension } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import StarterKit from '@tiptap/starter-kit'

import { createComposerCode } from '../../lib/composerCode'
import { docText, offsetAt, posAt, replaceText, textChange, textContent } from '../../lib/composerDoc'
import { createVoiceMarks, type VoiceMarkMeta, voiceMarksKey } from '../../lib/composerVoiceMarks'
import { plainRefs } from '../../lib/refChip'
import { createTokenChips, tokenChipsKey } from '../../lib/tokenChips'

const props = defineProps<{
  /** 标签上写的名字：handle → 人名、话题 id → 标题。 */
  names: RefNames
  placeholder?: string
  /** 读屏念的名字（没有可见标签的框）。 */
  label?: string
  title?: string
}>()

const emit = defineEmits<{
  (e: 'keydown', event: KeyboardEvent): void
  (e: 'paste', event: ClipboardEvent): void
  (e: 'compositionstart', event: CompositionEvent): void
  (e: 'compositionend', event: CompositionEvent): void
}>()

const text = defineModel<string>({ required: true })
const caret = defineModel<number>('caret', { default: 0 })

const host = ref<HTMLElement | null>(null)
const editor = shallowRef<Editor | null>(null)

// StarterKit 只留段落和撤销：其余的格式这一步不做，留着它们的快捷键和输入规则只会把
// 正文改成发出去以后读不懂的样子。
const OFF = [
  'blockquote',
  'bold',
  'bulletList',
  'code',
  'codeBlock',
  'dropcursor',
  'gapcursor',
  'hardBreak',
  'heading',
  'horizontalRule',
  'italic',
  'link',
  'listItem',
  'listKeymap',
  'orderedList',
  'strike',
  'trailingNode',
  'underline',
] as const

// Shift+Enter 换行：外面不接，它就是新起一行（没有它，浏览器会自己往里塞一个 <br>）。
const NewLine = Extension.create({
  name: 'composerNewLine',
  addKeyboardShortcuts() {
    return { 'Shift-Enter': () => this.editor.commands.splitBlock() }
  },
})

/** 把编辑器里的样子报给外面：正文和光标。 */
function report(ed: Editor) {
  if (applying) return
  // 不先和 text.value 比：外面绑着 v-model 时它要等外面重新渲染才变，同一拍里连着两次
  // 改动，第二次会被拿去和过时的值比掉。值没变时 defineModel 自己不发。
  text.value = docText(ed.state.doc)
  caret.value = offsetAt(ed.state.doc, ed.state.selection.head)
}

onMounted(() => {
  const ed = new Editor({
    element: host.value,
    extensions: [
      StarterKit.configure(Object.fromEntries(OFF.map((k) => [k, false]))),
      NewLine,
      createTokenChips({ names: () => props.names }),
      createComposerCode(),
      createVoiceMarks(),
    ],
    content: textContent(text.value),
    editorProps: {
      attributes: {
        class: 'composer-editor__input',
        role: 'textbox',
        'aria-multiline': 'true',
      },
      handleKeyDown(_view, event) {
        emit('keydown', event)
        return event.defaultPrevented
      },
      handlePaste(view, event) {
        emit('paste', event)
        if (event.defaultPrevented) return true
        const pasted = event.clipboardData?.getData('text/plain')
        if (!pasted) return false
        const { state } = view
        const from = offsetAt(state.doc, state.selection.from)
        const to = offsetAt(state.doc, state.selection.to)
        const body = pasted.replace(/\r\n?/g, '\n')
        const tr = replaceText(state.tr, state.schema, from, to, body)
        view.dispatch(tr.setSelection(TextSelection.create(tr.doc, posAt(tr.doc, from + body.length))).scrollIntoView())
        return true
      },
      // 复制到别处的是读得懂的字（@名字），不是 <@zhangsan>。
      clipboardTextSerializer: (slice) =>
        plainRefs(slice.content.textBetween(0, slice.content.size, '\n'), props.names),
      handleDOMEvents: {
        compositionstart: (_view, event) => {
          emit('compositionstart', event)
          return false
        },
        compositionend: (_view, event) => {
          emit('compositionend', event)
          return false
        },
      },
    },
    onTransaction: ({ editor: e }) => report(e),
  })
  editor.value = ed
  if (caret.value) moveCaret(caret.value)
  watchEffect(() => {
    // 提示语跟着名册变（队友的名字晚到），所以不写死在编辑器创建时的属性里。
    setAttr(ed.view.dom, 'aria-label', props.label)
    setAttr(ed.view.dom, 'title', props.title)
  })
})

function setAttr(el: HTMLElement, name: string, value: string | undefined) {
  if (value) el.setAttribute(name, value)
  else el.removeAttribute(name)
}

onBeforeUnmount(() => editor.value?.destroy())

function moveCaret(at: number) {
  const ed = editor.value
  if (!ed) return
  const { state } = ed.view
  ed.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, posAt(state.doc, at))))
}

// 外面改了正文或光标（挑中一个人、清空、换了话题的草稿）：只改不一样的那一段，其余
// 的字、标签和撤销记录都不动。两样一起看：外面常常是一口气改了正文又放了光标，先改
// 正文时编辑器报出去的光标会把外面刚放的那个盖掉。外面放了光标就照它放；只换了正文
// 的（起手草稿、换了话题恢复出来的草稿），光标落在末尾，接着往下打。
let applying = false
watch([text, caret], ([now, at], [, wasAt]) => {
  const ed = editor.value
  if (!ed) return
  const was = docText(ed.state.doc)
  const placed = at !== wasAt
  if (now !== was) {
    const change = textChange(was, now)
    applying = true
    try {
      ed.view.dispatch(replaceText(ed.state.tr, ed.state.schema, change.from, change.to, change.text))
    } finally {
      applying = false
    }
  }
  if (placed) {
    if (at !== offsetAt(ed.state.doc, ed.state.selection.head)) moveCaret(at)
    else report(ed)
  } else if (now !== was) moveCaret(now.length)
})
// 名册晚到、改了名：标签上的字跟着重画。
watch(
  () => props.names,
  () => {
    const ed = editor.value
    if (ed) ed.view.dispatch(ed.state.tr.setMeta(tokenChipsKey, true))
  }
)

defineExpose({
  focus() {
    editor.value?.view.focus()
  },
  /** 选中了几个字（粘贴会换掉它们）。 */
  selectedLength(): number {
    const ed = editor.value
    if (!ed) return 0
    const { doc, selection } = ed.state
    return offsetAt(doc, selection.to) - offsetAt(doc, selection.from)
  },
  /** 语音输入的两个记号（识别中的占位、刚落下的字），见 lib/composerVoiceMarks。 */
  voiceMarks(meta: VoiceMarkMeta) {
    const ed = editor.value
    if (ed) ed.view.dispatch(ed.state.tr.setMeta(voiceMarksKey, meta))
  },
  /** 识别中那个占位现在在正文的第几个字前面（人这期间接着打了字，它跟着走）。 */
  voiceSlot(): number | null {
    const ed = editor.value
    const slot = ed ? voiceMarksKey.getState(ed.state)?.slot : null
    return ed && slot != null ? offsetAt(ed.state.doc, slot) : null
  },
  /** 输入法正在组字：这时候不去改正文，否则会打断它。 */
  composing(): boolean {
    return !!editor.value?.view.composing
  },
})
</script>

<template>
  <div class="composer-editor">
    <div ref="host" class="composer-editor__host" />
    <div v-if="!text && placeholder" class="composer-editor__placeholder" aria-hidden="true">{{ placeholder }}</div>
  </div>
</template>

<style scoped>
.composer-editor {
  position: relative;
  min-width: 0;
}
.composer-editor__host :deep(.composer-editor__input) {
  /* 和消息流同一档（§3.2）：打出来的字和发出去以后的字一样大。 */
  font-size: 15px;
  line-height: var(--lh-15-reading);
  color: var(--text);
  /* 多一行长高一行，长到 --composer-lines 行封顶，再长就在框里滚。 */
  max-height: calc(var(--composer-lines, 6) * var(--lh-15-reading) + 12px);
  overflow-y: auto;
  padding: 6px 0;
  outline: none;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.composer-editor__host :deep(p) {
  margin: 0;
}
.composer-editor__placeholder {
  position: absolute;
  inset: 6px 0 auto;
  font-size: 15px;
  line-height: var(--lh-15-reading);
  color: var(--muted);
  pointer-events: none;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.composer-editor__host :deep(.composer-code),
.composer-editor__host :deep(.composer-code-line) {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--fill);
}
.composer-editor__host :deep(.composer-code) {
  border-radius: var(--radius-sm);
  padding: 1px 3px;
}
.composer-editor__host :deep(.composer-code-line) {
  padding: 0 8px;
  line-height: var(--lh-13);
}
.composer-editor__host :deep(.composer-code-line--fence) {
  color: var(--muted);
}
/* 录着、整理中：字会落在这儿。录着时一颗呼吸的红点 +「正在听」；整理中红点熄掉，
   「正在整理」上走一道扫光——机器正在做事，说得出正在发生的事（§9.6）。 */
.composer-editor__host :deep(.composer-voice-slot) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  margin: 0 2px;
  padding: 0 8px;
  vertical-align: -5px;
  border-radius: var(--radius-pill);
  background: var(--fill);
  color: var(--muted);
  font-size: 13px;
  line-height: 22px;
  user-select: none;
  animation: composer-voice-in var(--dur-base) var(--ease-out);
}
.composer-editor__host :deep(.composer-voice-slot i) {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--danger);
  animation: composer-voice-breathe 1.6s var(--ease-standard) infinite;
}
.composer-editor__host :deep(.composer-voice-slot--thinking i) {
  background: var(--faint);
  animation: none;
}
.composer-editor__host :deep(.composer-voice-slot--thinking span) {
  background: linear-gradient(90deg, var(--muted) 35%, var(--ink) 50%, var(--muted) 65%);
  background-size: 240% 100%;
  background-clip: text;
  color: transparent;
  animation: composer-voice-sheen 1.4s linear infinite;
}
/* 落字：整段先占好位置，从头逐字显出来，前沿四个字半透明；显的过程中垫着琥珀浅底，
   显完了 1.2 秒退掉——它从哪来，一眼看得到。 */
.composer-editor__host :deep(.composer-voice-revealing) {
  border-radius: 3px;
  background-color: var(--accent-wash);
}
.composer-editor__host :deep(.composer-voice-tail) {
  opacity: 0.35;
}
.composer-editor__host :deep(.composer-voice-pending) {
  opacity: 0;
}
.composer-editor__host :deep(.composer-voice-landed) {
  border-radius: 3px;
  animation: composer-voice-wash 1.2s var(--ease-standard) forwards;
}
@keyframes composer-voice-in {
  from {
    opacity: 0;
    transform: scale(0.92);
  }
}
@keyframes composer-voice-breathe {
  50% {
    opacity: 0.35;
  }
}
@keyframes composer-voice-sheen {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -40% 0;
  }
}
@keyframes composer-voice-wash {
  from {
    background-color: var(--accent-wash);
  }
  to {
    background-color: transparent;
  }
}
/* 关掉动画之后意思还在：红点是实心的，「正在整理」照样写着，浅底直接退掉。 */
@media (prefers-reduced-motion: reduce) {
  .composer-editor__host :deep(.composer-voice-slot),
  .composer-editor__host :deep(.composer-voice-slot i),
  .composer-editor__host :deep(.composer-voice-slot--thinking span) {
    animation: none;
  }
  .composer-editor__host :deep(.composer-voice-slot--thinking span) {
    color: var(--muted);
    background: none;
  }
}
</style>
