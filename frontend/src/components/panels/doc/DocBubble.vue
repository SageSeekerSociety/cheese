<script setup lang="ts">
// 选中文字后浮在上面的那一条：AI 队友、评论，能改时再加上块样式和几种字的格式、链接；
// 不能改时把格式换成「复制」。
//
// 手机上（`variant: 'bar'`）它是键盘上方的一条，编辑时一直在：没选中字时 AI 队友和
// 评论按不了，格式对着光标处要打的字；末尾多了撤销、重做（手机上没有快捷键）。
//
// 位置由 DocOverlays 算好递进来；这里只画，以及就地改格式。改格式的那一笔带着
// BUBBLE_META（lib/docBubble.ts）：浮条看到正文变了本该收起（光标底下的字换了），而这一笔是它自己改的，
// 选区还是那一段，浮条应当留着。
import type { ChainedCommands, Editor } from '@tiptap/core'

import { computed, onBeforeUnmount, ref, toRaw, watch } from 'vue'

import { useFocusReturn } from '@/composables/useFocusReturn'

import { BUBBLE_META } from '../../../lib/docBubble'
import { STATUS_KINDS } from '../../../lib/docSchema/blocks'
import { BLOCK_ITEMS, blockKeyOf } from '../../../lib/docSlashMenu'
import { canSetStatus, currentStatus, statusTransaction } from '../../../lib/docStatus'
import CheeseAvatar from '../../CheeseAvatar.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    editor: Editor
    agentName: string
    agentHandle?: string | null
    /** 能改：给格式和链接；不能改：给「复制」。 */
    editable: boolean
    /** 选中的是正文里的字（一段或几段）：给「正文 ▾」。不给时照编辑器里现在的选区算（键盘上方那一条）。 */
    restyle?: boolean
    /** 浮条上给不给「评论」：归档话题的文档不再收评论。 */
    canComment?: boolean
    /** 浮条上给不给 AI 队友。 */
    canAgent: boolean
    variant?: 'float' | 'bar'
    /** 选中了字（键盘上方那一条在没选中时也在）。 */
    hasSelection?: boolean
    /** 光标停在一个状态标签里：只给三种状态和「去掉」。 */
    statusOnly?: boolean
  }>(),
  { agentHandle: null, canComment: true, restyle: undefined, variant: 'float', hasSelection: true, statusOnly: false }
)
const emit = defineEmits<{
  (e: 'agent'): void
  (e: 'comment'): void
  (e: 'link'): void
  (e: 'copy'): void
}>()

// 正文每变一次重算哪几个格式亮着。
const revision = ref(0)
watch(
  () => toRaw(props.editor),
  (editor, _previous, cleanup) => {
    const update = () => revision.value++
    editor.on('transaction', update)
    cleanup(() => editor.off('transaction', update))
  },
  { immediate: true }
)

const MARKS = [
  { key: 'bold', icon: 'mdi-format-bold', mark: 'bold' },
  { key: 'italic', icon: 'mdi-format-italic', mark: 'italic' },
  { key: 'strike', icon: 'mdi-format-strikethrough', mark: 'strike' },
  { key: 'code', icon: 'mdi-code-tags', mark: 'code' },
  { key: 'highlight', icon: 'mdi-marker', mark: 'highlight' },
] as const

const BAR_MARKS = new Set(['bold', 'italic', 'highlight'])
const marks = computed(() => {
  void revision.value
  const editor = toRaw(props.editor)
  // 键盘上方那一条地方小，只留最常用的三样。
  const shown = props.variant === 'bar' ? MARKS.filter((item) => BAR_MARKS.has(item.key)) : MARKS
  return shown.map((item) => ({
    ...item,
    active: editor.isActive(item.mark),
    disabled: !editor.can().toggleMark(item.mark),
  }))
})

// 键盘上方那一条：在手机上只有它能撤销。只看撤销栈，不分在不在格式化。
const canUndo = computed(() => {
  void revision.value
  return toRaw(props.editor).can().undo()
})
const canRedo = computed(() => {
  void revision.value
  return toRaw(props.editor).can().redo()
})

function format(run: (chain: ChainedCommands) => ChainedCommands) {
  const editor = toRaw(props.editor)
  run(editor.chain().focus(undefined, { scrollIntoView: false }).setMeta(BUBBLE_META, true)).run()
}

// ---- 状态标签：和加粗一样是字的样式。选中的字、或者光标所在的那个标签，换成这一种；
// 已经是这一种就去掉。
const STATUS_ORDER = Object.keys(STATUS_KINDS) as (keyof typeof STATUS_KINDS)[]
const statuses = computed(() => {
  void revision.value
  const { state } = toRaw(props.editor)
  const current = currentStatus(state)
  const can = canSetStatus(state)
  return STATUS_ORDER.map((kind) => ({ kind, mark: STATUS_KINDS[kind], active: current === kind, disabled: !can }))
})
function applyStatus(kind: keyof typeof STATUS_KINDS | null) {
  const editor = toRaw(props.editor)
  const tr = statusTransaction(editor.state, kind)
  if (tr) editor.view.dispatch(tr)
}

// ---- 「正文 ▾」：把选中的这一块（或者这几块）换成别的块。和 slash 菜单是同一张表，
// 去掉插入新东西的那几项（表格、分隔线）。
const blockMenu = computed(() => {
  if (props.restyle !== undefined) return props.restyle
  void revision.value
  return toRaw(props.editor).state.selection.$from.depth > 0
})
const blockOpen = ref(false)
const currentBlock = computed(() => {
  void revision.value
  const { $from } = toRaw(props.editor).state.selection
  const key = blockKeyOf($from.depth > 0 ? $from.node(1) : null)
  return BLOCK_ITEMS.find((item) => item.key === key) ?? BLOCK_ITEMS[0]
})
function pickBlock(run: (chain: ChainedCommands) => ChainedCommands) {
  blockOpen.value = false
  format(run)
}
function closeBlocks(e: MouseEvent) {
  if (!(e.target as HTMLElement | null)?.closest('.doc-bubble__blocks')) blockOpen.value = false
}
watch(blockOpen, (open) => {
  if (open) document.addEventListener('mousedown', closeBlocks, true)
  else document.removeEventListener('mousedown', closeBlocks, true)
})
onBeforeUnmount(() => document.removeEventListener('mousedown', closeBlocks, true))

// 菜单关上时把焦点还回先前拿着焦点的地方（多半是正文编辑器）。
useFocusReturn(blockOpen)
</script>

<template>
  <div
    class="doc-bubble"
    :class="`doc-bubble--${variant}`"
    role="toolbar"
    :aria-label="t('work.room.doc.selectionToolbar')"
    @mousedown.prevent
  >
    <template v-if="statusOnly">
      <button
        v-for="item in statuses"
        :key="item.kind"
        type="button"
        class="doc-bubble__icon doc-bubble__status"
        :class="`doc-bubble__status--${item.kind}`"
        :aria-label="t(`work.room.doc.blocks.statusKinds.${item.kind}`)"
        :title="t(`work.room.doc.blocks.statusKinds.${item.kind}`)"
        :aria-pressed="item.active"
        @click="applyStatus(item.kind)"
      >
        {{ item.mark }}
      </button>
      <span class="doc-bubble__sep" aria-hidden="true" />
      <button type="button" @click="applyStatus(null)">{{ t('work.room.doc.blocks.statusOff') }}</button>
    </template>
    <template v-else>
      <button
        v-if="canAgent"
        type="button"
        class="doc-bubble__agent"
        :aria-label="agentName"
        :disabled="!hasSelection"
        @click="emit('agent')"
      >
        <CheeseAvatar :size="16" :name="agentName" :handle="agentHandle" />
        {{ agentName }}
      </button>
      <span v-if="canAgent && canComment" class="doc-bubble__sep" aria-hidden="true" />
      <button
        v-if="canComment"
        type="button"
        :aria-label="t('work.room.doc.commentOnSelection')"
        :disabled="!hasSelection"
        @click="emit('comment')"
      >
        {{ t('work.room.comments.comment') }}
      </button>
      <template v-if="editable">
        <span class="doc-bubble__sep" aria-hidden="true" />
        <div v-if="blockMenu" class="doc-bubble__blocks">
          <button
            type="button"
            :aria-label="t('work.room.doc.blockType')"
            aria-haspopup="menu"
            :aria-expanded="blockOpen"
            @click="blockOpen = !blockOpen"
          >
            {{ currentBlock.label }}
            <v-icon size="14">mdi-chevron-down</v-icon>
          </button>
          <Transition name="doc-menu">
            <div v-if="blockOpen" class="doc-menu doc-bubble__menu" role="menu">
              <button
                v-for="item in BLOCK_ITEMS"
                :key="item.key"
                type="button"
                role="menuitemradio"
                :aria-checked="item.key === currentBlock.key"
                class="doc-menu__item"
                @click="pickBlock(item.run)"
              >
                <v-icon size="16">{{ item.icon }}</v-icon>
                {{ item.label }}
              </button>
            </div>
          </Transition>
        </div>
        <button
          v-for="item in marks"
          :key="item.key"
          type="button"
          class="doc-bubble__icon"
          :aria-label="t(`work.room.doc.format.${item.key}`)"
          :title="t(`work.room.doc.format.${item.key}`)"
          :aria-pressed="item.active"
          :disabled="item.disabled"
          @click="format((c) => c.toggleMark(item.mark))"
        >
          <v-icon size="17">{{ item.icon }}</v-icon>
        </button>
        <span class="doc-bubble__sep" aria-hidden="true" />
        <button
          v-for="item in statuses"
          :key="item.kind"
          type="button"
          class="doc-bubble__icon doc-bubble__status"
          :class="`doc-bubble__status--${item.kind}`"
          :aria-label="t(`work.room.doc.blocks.statusKinds.${item.kind}`)"
          :title="t(`work.room.doc.blocks.statusKinds.${item.kind}`)"
          :aria-pressed="item.active"
          :disabled="item.disabled"
          @click="applyStatus(item.kind)"
        >
          {{ item.mark }}
        </button>
        <button
          v-if="variant === 'float'"
          type="button"
          class="doc-bubble__icon"
          :aria-label="t('work.room.docLink.title')"
          :title="t('work.room.docLink.title')"
          @click="emit('link')"
        >
          <v-icon size="17">mdi-link-variant</v-icon>
        </button>
        <template v-else>
          <span class="doc-bubble__sep" aria-hidden="true" />
          <button
            type="button"
            class="doc-bubble__icon"
            :aria-label="t('work.room.doc.undo')"
            :disabled="!canUndo"
            @click="format((c) => c.undo())"
          >
            <v-icon size="17">mdi-undo</v-icon>
          </button>
          <button
            type="button"
            class="doc-bubble__icon"
            :aria-label="t('work.room.doc.redo')"
            :disabled="!canRedo"
            @click="format((c) => c.redo())"
          >
            <v-icon size="17">mdi-redo</v-icon>
          </button>
        </template>
      </template>
      <button v-else type="button" :disabled="!hasSelection" @click="emit('copy')">
        {{ t('work.room.doc.copy') }}
      </button>
    </template>
  </div>
</template>

<style scoped>
/* 浮在正文上的一条，和别的浮层一个样子：浮层底色、描边、投影。 */
.doc-bubble {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 3px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--raised);
  box-shadow: var(--shadow-2);
  white-space: nowrap;
}
.doc-bubble button:not(.doc-menu__item) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  border: none;
  background: transparent;
  color: inherit;
  padding: 0 9px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-bubble button:not(.doc-menu__item):hover:not(:disabled),
.doc-bubble button:not(.doc-menu__item)[aria-pressed='true'],
.doc-bubble button:not(.doc-menu__item)[aria-expanded='true'] {
  background: var(--fill);
}
.doc-bubble button[aria-pressed='true'] {
  color: var(--ink);
}
.doc-bubble button:disabled {
  opacity: 0.4;
  cursor: default;
}
.doc-bubble button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.doc-bubble .doc-bubble__icon {
  width: 30px;
  padding: 0;
  justify-content: center;
}
.doc-bubble__agent {
  font-weight: 600;
}
/* 三种状态各用它在正文里的颜色，一眼认得出是哪一种。 */
.doc-bubble__status {
  font-weight: 700;
}
.doc-bubble__status--ok {
  color: var(--ok-ink);
}
.doc-bubble__status--no {
  color: var(--danger-ink);
}
.doc-bubble__status--warn {
  color: var(--warn-ink);
}
/* 手机上键盘上方的那一条：浅色，按钮按手指的大小，放不下时横着滑。 */
.doc-bubble--bar {
  width: 100%;
  gap: 0;
  padding: 2px 4px;
  border-top: 1px solid var(--line-2);
  border-radius: 0;
  background: var(--surface);
  box-shadow: none;
  color: var(--text);
  overflow-x: auto;
}
.doc-bubble--bar button {
  flex: 0 0 auto;
  height: 44px;
  font-size: 14px;
}
.doc-bubble--bar .doc-bubble__icon {
  width: 44px;
}
.doc-bubble--bar .doc-bubble__sep {
  flex: 0 0 auto;
}
/* 键盘上方那一条在屏幕最下面：块样式的菜单往上开。 */
.doc-bubble--bar .doc-bubble__menu {
  --doc-menu-from: 4px;
  position: fixed;
  top: auto;
  bottom: calc(var(--doc-keyboard-bar-bottom, 0px) + 52px);
  left: 8px;
}
.doc-bubble__sep {
  width: 1px;
  height: 16px;
  margin: 0 3px;
  background: var(--line-2);
}
.doc-bubble__blocks {
  position: relative;
}
/* 块样式的菜单：外观在 styles/docBlocks.css 的 .doc-menu，这里只管摆在哪。 */
.doc-bubble__menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
}
</style>
