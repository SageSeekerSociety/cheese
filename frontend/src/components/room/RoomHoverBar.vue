<script setup lang="ts">
// 消息的悬停条：整列只有这一个，停在当前消息 header 的右侧动作位置。
//
// 原来每条消息各带一个，指针换一行就是旧的淡出、新的淡入，两处同时在动，而且看
// 不出它们是同一个东西。只留一个之后，它在行与行之间滑过去，只有移出整列时才淡
// 出。现在行内 header 给动作留了位置，换行直接落到对应区域，不穿过消息正文。
// 表情选择条挂在它下面，一起走。
//
// 它不认识时间线：停在哪条消息上、离顶多远，都是房间算好传进来的。
import type { Block } from '../../cx_types'
import type { MenuAction } from '../common/menuAction'

import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import AdaptiveMenu from '../common/AdaptiveMenu.vue'

import { copyMessage, QUICK_EMOJIS } from './messageActions'

import { t } from '@/i18n'

const props = defineProps<{
  /** 停在哪条消息上。收起时还留着上一条，淡出的那一下里按钮不会先没了。 */
  block: Block | null
  shown: boolean
  /** 那条消息的顶边离时间线内容顶部多少 px。 */
  top: number
  /** 从收起状态出现：这一下直接落到位，不从上一次的位置滑过来。 */
  jump: boolean
  isAgent: boolean
  pickerOpen: boolean
  /** 这条是我自己发的消息：多一颗「编辑」。 */
  editable: boolean
}>()

const emit = defineEmits<{
  (e: 'react', block: Block, emoji: string): void
  (e: 'toggle-picker', blockId: string): void
  (e: 'reply', block: Block): void
  (e: 'upgrade', blockId: string): void
  (e: 'edit', block: Block): void
}>()

// 复制之后原地说一声「已复制」，一会儿再换回来；换了一条消息就不再说。
const COPIED_MS = 1500
const copied = ref(false)
const menuOpen = ref(false)
const focusWithin = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined
let focusRecoveryFrame: number | undefined
function cancelFocusRecovery() {
  if (focusRecoveryFrame !== undefined) cancelAnimationFrame(focusRecoveryFrame)
  focusRecoveryFrame = undefined
}
watch(
  () => props.block?.id,
  () => {
    copied.value = false
    clearTimeout(copiedTimer)
    if (!props.block) {
      cancelFocusRecovery()
      menuOpen.value = false
      focusWithin.value = false
    }
  }
)
onBeforeUnmount(() => {
  clearTimeout(copiedTimer)
  cancelFocusRecovery()
})

async function copy() {
  await copyBlock(props.block, props.isAgent)
}
async function copyBlock(block: Block | null, isAgent: boolean) {
  if (!block || !(await copyMessage(block, isAgent))) return
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), COPIED_MS)
}

// 浮层里鼠标已不在消息上：操作仍属于打开菜单时的那一条。
const menuTarget = shallowRef<{ block: Block; isAgent: boolean; editable: boolean } | null>(null)
watch(
  menuOpen,
  (open) => {
    if (open && props.block) menuTarget.value = { block: props.block, isAgent: props.isAgent, editable: props.editable }
  },
  { flush: 'sync' }
)
const menuActions = computed<MenuAction[]>(() => {
  const target = menuTarget.value
  if (!target) return []
  const block = target.block
  const actions: MenuAction[] = [
    {
      key: 'reply',
      label: t('work.room.message.reply'),
      icon: 'mdi-reply-outline',
      onSelect: () => emit('reply', block),
    },
    {
      key: 'copy',
      label: t('work.room.message.copy'),
      icon: 'mdi-content-copy',
      onSelect: () => void copyBlock(block, target.isAgent),
    },
  ]
  if (target.editable)
    actions.push({
      key: 'edit',
      label: t('work.room.message.edit'),
      icon: 'mdi-pencil-outline',
      onSelect: () => emit('edit', block),
    })
  actions.push({
    key: 'upgrade',
    label: t('work.room.message.upgrade'),
    icon: 'mdi-comment-arrow-right-outline',
    onSelect: () => emit('upgrade', block.id),
  })
  return actions
})
function menuReact(emoji: string) {
  if (!menuTarget.value) return
  menuOpen.value = false
  emit('react', menuTarget.value.block, emoji)
}
function onFocusOut(event: FocusEvent) {
  cancelFocusRecovery()
  const bar = event.currentTarget as HTMLElement
  focusWithin.value = event.relatedTarget instanceof Node && bar.contains(event.relatedTarget)
  const previous = event.target
  // CSS container缩窄会隐藏当前宽栏按钮，浏览器把焦点交给body。
  // 只接回这一次属于本栏的失焦；用户已选了外部控件时不迁移。
  if (
    event.relatedTarget !== null ||
    !(previous instanceof HTMLElement) ||
    !previous.closest('.hover-bar__wide') ||
    previous.getClientRects().length
  )
    return
  const blockId = props.block?.id
  focusRecoveryFrame = requestAnimationFrame(() => {
    focusRecoveryFrame = undefined
    if (
      !bar.isConnected ||
      !previous.isConnected ||
      !bar.contains(previous) ||
      !blockId ||
      props.block?.id !== blockId ||
      previous.getClientRects().length ||
      !document.hasFocus() ||
      (document.activeElement !== document.body && document.activeElement !== previous)
    )
      return
    const more = bar.querySelector<HTMLButtonElement>('.hover-bar__more')
    if (more?.getClientRects().length) more.focus({ preventScroll: true })
  })
}
</script>

<template>
  <div
    class="hover-bar"
    :class="{ 'hover-bar--shown': shown || menuOpen || focusWithin, 'hover-bar--jump': jump }"
    :style="{ transform: `translateY(${top}px)` }"
    :aria-hidden="!shown && !menuOpen && !focusWithin"
    @focusin="focusWithin = true"
    @focusout="onFocusOut"
  >
    <template v-if="block">
      <div class="hover-bar__wide">
        <button
          type="button"
          class="hover-bar__act rx-toggle"
          :class="{ 'hover-bar__act--on': pickerOpen }"
          :title="t('work.room.message.react')"
          @click="emit('toggle-picker', block.id)"
        >
          <v-icon size="15">mdi-emoticon-happy-outline</v-icon>
        </button>
        <button
          type="button"
          class="hover-bar__act"
          :title="t('work.room.message.reply')"
          @click="emit('reply', block)"
        >
          <v-icon size="15">mdi-reply-outline</v-icon>
        </button>
        <button
          v-if="editable"
          type="button"
          class="hover-bar__act"
          :title="t('work.room.message.edit')"
          @click="emit('edit', block)"
        >
          <v-icon size="15">mdi-pencil-outline</v-icon>
        </button>
        <button
          type="button"
          class="hover-bar__act"
          :title="copied ? t('work.room.message.copied') : t('work.room.message.copy')"
          @click="copy"
        >
          <v-icon size="15">{{ copied ? 'mdi-check' : 'mdi-content-copy' }}</v-icon>
        </button>
        <button
          type="button"
          class="hover-bar__act"
          :title="t('work.room.message.upgrade')"
          @click="emit('upgrade', block.id)"
        >
          <v-icon size="15">mdi-comment-arrow-right-outline</v-icon>
        </button>
      </div>
      <AdaptiveMenu v-model="menuOpen" :actions="menuActions">
        <template #activator="{ props: menu }">
          <button
            v-bind="menu"
            type="button"
            class="hover-bar__act hover-bar__more"
            :title="t('work.room.composer.more')"
            :aria-label="t('work.room.composer.more')"
          >
            <v-icon size="15">mdi-dots-horizontal</v-icon>
          </button>
        </template>
        <template #desktopHeader>
          <div class="hover-menu__emojis" role="group" :aria-label="t('work.room.message.react')">
            <button v-for="emoji in QUICK_EMOJIS" :key="emoji" type="button" class="rx-pick" @click="menuReact(emoji)">
              {{ emoji }}
            </button>
          </div>
        </template>
        <template #header>
          <div class="hover-menu__emojis" role="group" :aria-label="t('work.room.message.react')">
            <button v-for="emoji in QUICK_EMOJIS" :key="emoji" type="button" class="rx-pick" @click="menuReact(emoji)">
              {{ emoji }}
            </button>
          </div>
        </template>
      </AdaptiveMenu>
      <!-- MVP emoji picker: the 8 common reactions, Slack-style. 从那颗按钮下面长
           出来，收回也回到那里。 -->
      <Transition name="rx-picker">
        <div v-if="pickerOpen" class="rx-picker">
          <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="rx-pick" @click="emit('react', block, e)">
            {{ e }}
          </button>
        </div>
      </Transition>
    </template>
  </div>
</template>

<style scoped>
/* 挂在时间线内容层，落在消息 header 留好的动作位置。换行直接到位，避免动画
   穿过其他消息时截获正文点击；出现和收起只改透明度。 */
.hover-bar {
  position: absolute;
  top: 4px;
  right: 16px;
  /* 滚动锚定会选中它：这一条跟着指针在行间改 translateY，改多少这一栏就跟着滚多少，
     鼠标扫过消息时页面瞬移（量到 scrollTop 784 → 322 = 同一帧里 translateY 1200 →
     738）。它是绝对定位的装饰，不参与内容排版，排除出锚点选取没有代价；内容自己的
     锚定照旧，「往上翻拼进一页」那一下的补偿还靠它。 */
  overflow-anchor: none;
  z-index: 4;
  display: flex;
  gap: 2px;
  padding: 0;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--dur-quick) var(--ease-in);
}
.hover-bar--shown {
  opacity: 1;
  pointer-events: auto;
  transition: opacity var(--dur-quick) var(--ease-out);
}
/* 从收起状态出现时直接落到位，只淡入：从上一次停的那一行滑过来，说的是一件没
   发生的事。 */
.hover-bar--jump {
  transition: opacity var(--dur-quick) var(--ease-out);
}
/* One quiet square button per action: muted ink, fill on hover. */
.hover-bar__act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.hover-bar__act:hover {
  background: var(--fill);
  color: var(--ink);
}
.hover-bar__act--on {
  background: var(--line-2);
  color: var(--ink);
}
.hover-bar__wide {
  display: contents;
}
.hover-bar__more {
  display: none;
}
/* Esc 从浮层回到更多入口后，指针可能已在列外。列变宽也不藏掉当前
   焦点；离开入口才恢复完整动作，避免把键盘留在不可见按钮上。 */
.hover-bar__more:focus {
  display: inline-flex;
}
.hover-bar:has(.hover-bar__more:focus) .hover-bar__wide {
  display: none;
}
@container chat-timeline (width < 310px) {
  .hover-bar__wide {
    display: none;
  }
  .hover-bar__more {
    display: inline-flex;
  }
}
.hover-menu__emojis {
  display: flex;
  gap: 2px;
  padding: 4px;
}
.rx-picker-enter-active {
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.rx-picker-leave-active {
  transition:
    transform var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.rx-picker-enter-from,
.rx-picker-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
.rx-picker {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 5;
  display: flex;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  transform-origin: top right;
}
.rx-pick {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  /* 这个 16px 量的是一枚 emoji 字形，不是正文，所以不走字号阶梯；`line-height: 1`
     同理——它是把字形在 28px 方格里居中的手段，不是一段话的行距。 */
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.rx-pick:hover {
  background: var(--fill);
}
</style>
