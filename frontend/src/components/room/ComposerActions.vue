<script setup lang="ts">
// 输入框下面那一行：左边「+」，右边依次是麦克风、AI 队友头像、发送。
//
// 桌面和手机是同一个结构，只是尺寸不同。左边是往这条消息里放东西的入口——上传文件、
// 照片或视频、清单、提醒、键盘快捷键都收在「+」里；右边是「这条怎么发出去」：说一段、
// 交给谁、发。麦克风挨着发送，录音时录音条从它的位置往左展开（外面的 `voice` 插槽），
// 发送键原地变成 ✓，按下和说完都在右下角这一块，手机上拇指正好够得着。
//
// 发送是这一行唯一的实心按钮，其余一律是安静的图标。
//
// 从 `RoomComposer.vue` 里拆出来的（#2143）。它只吃 props、只往上发事件：挑文件、发送、
// 录音，几件事都不在这里决定。交给谁那颗头像由外面从 `summon` 插槽交进来。
import type { MenuAction } from '@/components/common/menuAction'
import type { VoiceState } from '@/composables/useVoiceInput'

import { computed, onBeforeUnmount, ref, useSlots } from 'vue'
import { useEventListener } from '@vueuse/core'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { openShortcutSheet } from '@/components/common/shortcutSheet'
import { t } from '@/i18n'

const props = defineProps<{
  /** 附件还在上传：那一刻发不出去，灰着而不是把字吞掉。 */
  uploading: boolean
  /** 现在能不能发（有字、有附件，或者攒着待发批注）。 */
  canSend: boolean
  /** 攒着几条待发批注：发送键上写清这一下带几条。 */
  pendingCount?: number
  /** 「+」里有没有「照片或视频」——桌面上贴一张截图或者拖进来就完事了。 */
  showImagePicker: boolean
  /** 回车是发送（触摸屏上是换行），这一行的间距跟着它走。 */
  enterSends: boolean
  /** 界面导览能指发送键（见 RoomComposer 的 `guided`）。 */
  guided?: boolean
  /** 能不能在这儿发一张清单（房间给了发清单的路才有这一项）。 */
  canChecklist?: boolean
  /** 「提醒我」那一项。房间还没定下来（没有话题）时不给。 */
  canRemind?: boolean
  /** 有没有麦克风这一颗（这个房间能语音输入）。 */
  canVoice?: boolean
  /** 录音进行到哪一步：录着时发送键是 ✓，整理中转圈。 */
  voice?: VoiceState
  /** 刚说进来一段：能换成哪一种写法（`raw` 原话 / `polished` 整理后的）；没有就是 null。 */
  voiceSwap?: 'raw' | 'polished' | null
  /** 这里不收文件（题目页问芝士）：「+」不画。 */
  noFiles?: boolean
  /** 「+」里传文件那一项的字（芝士面板走的是另一条上传，限额不同）；不给就是频道的那句。 */
  filesLabel?: string
  /** 对方正在答（芝士面板）：发送键换成停止。 */
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'files', files: File[]): void
  (e: 'checklist'): void
  (e: 'remind'): void
  (e: 'send'): void
  /** 录着的时候按了发送键那个位置上的 ✓：说完了。 */
  (e: 'voice-done'): void
  /**
   * 麦克风：`tap` 点一下开始，`hold` 按住开始（带按着的那个指针和按下时的横坐标），
   * `release` 按住之后松手，`cancel` 系统收走了这个指针。
   */
  (e: 'voice', how: 'tap' | 'hold' | 'release' | 'cancel', pointerId?: number, x?: number): void
  (e: 'voice-swap'): void
  (e: 'stop'): void
}>()

const slots = useSlots()
const voiceOpen = computed(() => !!props.voice && props.voice !== 'idle')

const fileInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) emit('files', Array.from(input.files))
  input.value = '' // allow re-picking the same file
}

// 触屏上没有键盘，「键盘快捷键」那一项在那儿是一句空话。按输入方式判断，不按视口宽度
// （和 useChatRowActions 同一条判据）：插上鼠标或触控板的平板，`hover: none` 不再匹配，它自己回来。
const touchQuery = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
const touchOnly = ref(!!touchQuery?.matches)
useEventListener(touchQuery, 'change', (e: MediaQueryListEvent) => (touchOnly.value = e.matches))

const plusOpen = ref(false)
const plusActions = computed<MenuAction[]>(() => {
  const list: MenuAction[] = [
    {
      key: 'files',
      label: props.filesLabel ?? t('work.room.composer.attachFiles'),
      icon: 'mdi-paperclip',
      onSelect: () => fileInput.value?.click(),
    },
  ]
  // 手机上单开相册入口：那儿没有截图可贴、也没有东西可拖，从文件选择器里翻相册要绕好几步。
  if (props.showImagePicker)
    list.push({
      key: 'photos',
      label: t('work.room.composer.sendPhotos'),
      icon: 'mdi-image-outline',
      onSelect: () => imageInput.value?.click(),
    })
  if (props.canChecklist)
    list.push({
      key: 'checklist',
      label: t('work.room.checklist.compose'),
      icon: 'mdi-format-list-checks',
      onSelect: () => emit('checklist'),
    })
  if (props.canRemind)
    list.push({
      key: 'remind',
      label: t('work.room.reminder.open'),
      icon: 'mdi-bell-outline',
      onSelect: () => emit('remind'),
    })
  if (!touchOnly.value)
    list.push({
      key: 'shortcuts',
      label: t('global.shortcuts.open'),
      icon: 'mdi-keyboard-outline',
      divider: list.length > 1,
      onSelect: openShortcutSheet,
    })
  return list
})

// 麦克风：点一下是开始（再点 ✓ 结束），按住超过 250ms 是「按住说话」，松手就结束。
// 指针一按下就抓住它，手指滑出按钮再松开也算松手。键盘的回车/空格走 click（detail 为 0），
// 鼠标和手指走 pointer，两条路不会各算一次。
const HOLD_MS = 250
let holdTimer = 0
let held = false
// 按着这颗麦克风的那根手指。只认它：录音结束、动作行重新出现后，原来那根手指的松手
// 会落在新画出来的麦克风上，它没按过，不能当成一次轻点又开一段录音。
let downId: number | null = null
function micDown(e: PointerEvent) {
  if (e.button !== 0 || downId !== null) return
  downId = e.pointerId
  held = false
  ;(e.currentTarget as HTMLElement | null)?.setPointerCapture?.(e.pointerId)
  const pointer = e.pointerId
  const x = e.clientX
  holdTimer = window.setTimeout(() => {
    held = true
    emit('voice', 'hold', pointer, x)
  }, HOLD_MS)
}
function micUp(e: PointerEvent) {
  if (e.button !== 0 || e.pointerId !== downId) return
  window.clearTimeout(holdTimer)
  downId = null
  const wasHeld = held
  held = false
  emit('voice', wasHeld ? 'release' : 'tap')
}
function micCancel(e: PointerEvent) {
  if (e.pointerId !== downId) return
  window.clearTimeout(holdTimer)
  downId = null
  const wasHeld = held
  held = false
  if (wasHeld) emit('voice', 'cancel')
}
function micKey(e: MouseEvent) {
  if (e.detail === 0) emit('voice', 'tap')
}
onBeforeUnmount(() => window.clearTimeout(holdTimer))

function onSend() {
  if (props.voice === 'recording') emit('voice-done')
  else if (!voiceOpen.value) emit('send')
}
const sendLabel = computed(() => {
  if (props.voice === 'recording') return t('work.room.composer.voice.done')
  if (props.voice === 'thinking') return t('work.room.composer.voice.thinking')
  if (props.uploading) return t('work.room.composer.sendUploading')
  if (props.pendingCount && props.pendingCount > 0)
    return t('work.room.preview.sendWithNotes', { count: props.pendingCount })
  return t('work.room.composer.send')
})
/** 只在这一颗键要带字的时候给（攒着待发批注）：录音、整理、上传那几步仍然只显示图标，
 *  否则「说完了 ✓」和「发送（含 2 条批注）」会在同一颗键上抢位置。 */
const pendingLabel = computed(() =>
  !voiceOpen.value &&
  !props.uploading &&
  props.voice !== 'recording' &&
  props.voice !== 'thinking' &&
  props.pendingCount &&
  props.pendingCount > 0
    ? sendLabel.value
    : ''
)
// 整理中不禁用：禁用的键会褪成灰色，而这一刻它是唯一在说「机器正在做」的地方，要保持琥珀色
// 转圈。点它什么也不做（onSend 里拦着），读屏由 aria-disabled 说明。
const sendDisabled = computed(() => {
  if (voiceOpen.value) return false
  return props.uploading || !props.canSend
})
</script>

<template>
  <div
    class="composer-actions"
    :class="{
      'composer-actions--touch': !enterSends,
      'composer-actions--summon': !!slots.summon,
      'composer-actions--voice': voiceOpen,
    }"
  >
    <!-- 这两个 input 是藏起来的，但**不能**用 display:none / visibility:hidden：
             iOS Safari 拒绝用脚本打开一个被隐藏掉的文件选择框，按钮点下去
             毫无反应。所以按 .visually-hidden 的老办法藏——留在布局里、只是
             看不见。旁边 components/common/FileSelect.vue 里也是这么藏的。
             They are opened only by script from the menu below, so
             keep them out of the Tab order. -->
    <input
      ref="fileInput"
      type="file"
      multiple
      class="visually-hidden"
      tabindex="-1"
      :aria-label="t('work.room.composer.attachFiles')"
      @change="onFilePicked"
    />
    <input
      ref="imageInput"
      type="file"
      accept="image/*,video/*"
      multiple
      class="visually-hidden"
      tabindex="-1"
      :aria-label="t('work.room.composer.sendPhotos')"
      @change="onFilePicked"
    />
    <div class="composer-actions__start">
      <AdaptiveMenu v-if="!noFiles" v-model="plusOpen" :actions="plusActions" location="top start">
        <template #activator="{ props: menu }">
          <BaseButton
            v-bind="menu"
            kind="ghost"
            class="composer-icon composer-plus"
            :class="{ 'composer-plus--open': plusOpen }"
            icon="mdi-plus"
            size="sm"
            :title="t('work.room.composer.more')"
            :aria-label="t('work.room.composer.more')"
          />
        </template>
      </AdaptiveMenu>
      <!-- 刚说进来一段：换成原话 / 换回整理后的。底下那条细线走完就收起，正文一动也走开。 -->
      <Transition name="voice-swap">
        <button v-if="voiceSwap && !voiceOpen" type="button" class="voice-swap" @click="emit('voice-swap')">
          <v-icon size="14">{{ voiceSwap === 'raw' ? 'mdi-undo-variant' : 'mdi-auto-fix' }}</v-icon>
          {{ voiceSwap === 'raw' ? t('work.room.composer.voice.useRaw') : t('work.room.composer.voice.usePolished') }}
          <i :key="voiceSwap" class="voice-swap__left" aria-hidden="true" />
        </button>
      </Transition>
    </div>
    <span class="composer-actions__spacer" />
    <!-- 话题的状态（已归档）从外面交进来。它是状态不是动作，所以最安静。 -->
    <slot name="chips" />
    <div class="composer-actions__end">
      <!-- 麦克风：说一段，整理成字落在光标处。挨着发送：录音条从这里往左长，✓ 就在旁边。 -->
      <BaseButton
        v-if="canVoice"
        kind="ghost"
        class="composer-icon composer-mic"
        icon="mdi-microphone-outline"
        size="sm"
        :title="t('work.room.composer.voice.start')"
        :aria-label="t('work.room.composer.voice.start')"
        @pointerdown="micDown"
        @pointerup="micUp"
        @pointercancel="micCancel"
        @click="micKey"
        @contextmenu.prevent
      />
      <slot name="summon" />
      <!-- 断线时照样能发：消息进发件箱、立刻显示，连上就自己走 (§14.1)。附件还在传时是
           例外：这一刻发出去会少带附件，所以要等，并在 title 里说清为什么按不动。
           录音时它原地变成 ✓（说完了），整理中转圈。 -->
      <BaseButton
        v-if="busy"
        class="composer-send"
        kind="primary"
        size="sm"
        icon="mdi-stop"
        :title="t('work.room.composer.stop')"
        :aria-label="t('work.room.composer.stop')"
        @click="emit('stop')"
      />
      <!-- 攒着待发批注时这颗键带字（`发送（含 N 条批注）`）：只写进 title 的话，
           读者在按下之前看不到这一下会带走几条。其余时候仍是纯图标。 -->
      <BaseButton
        v-else
        v-guide-anchor="guided ? 'composer-send' : ''"
        class="composer-send"
        :class="{ 'composer-send--labelled': pendingLabel }"
        kind="primary"
        size="sm"
        :title="sendLabel"
        :aria-label="sendLabel"
        :disabled="sendDisabled"
        :aria-disabled="voice === 'thinking' ? 'true' : undefined"
        @click="onSend"
      >
        <span v-if="pendingLabel" class="composer-send__label">{{ pendingLabel }}</span>
        <span v-else class="composer-send__face">
          <v-icon :class="{ 'composer-send__icon--off': voiceOpen }" size="16">mdi-send</v-icon>
          <v-icon :class="{ 'composer-send__icon--off': voice !== 'recording' }" size="18">mdi-check</v-icon>
          <span
            class="composer-send__spin"
            :class="{ 'composer-send__icon--off': voice !== 'thinking' }"
            aria-hidden="true"
          />
        </span>
      </BaseButton>
    </div>
    <!-- 录音条：从麦克风的位置往左展开，盖住「+」，停在麦克风的右边缘，不盖头像和发送。 -->
    <div class="composer-actions__voice">
      <slot name="voice" />
    </div>
  </div>
</template>

<style scoped>
/* 动作行的规矩，三条：
   1. 一行一个高度：28px。
   2. 静止时谁也不画边框、不画底色——状态用墨色说，不用盒子说。
   3. 整行只有一个实心块，就是发送。 */
.composer-actions {
  --gap: 4px;
  position: relative;
  display: flex;
  align-items: center;
  min-height: 28px;
  margin-top: 2px;
}
.composer-actions__start,
.composer-actions__end {
  display: flex;
  align-items: center;
  gap: var(--gap);
}
.composer-actions__spacer {
  flex: 1;
}
.composer-icon,
.composer-send {
  width: 28px;
  height: 28px;
}
.composer-send {
  min-width: 0;
  padding: 0;
}
/* 带字的时候不再固定 28px 的方：让那颗键按字长出来。 */
.composer-send--labelled {
  width: auto;
  padding: 0 10px;
}
.composer-send__label {
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}
/* 「+」打开时转成 ×：同一颗按钮，开和关是一件事的两头。 */
.composer-plus :deep(.v-icon) {
  transition: transform var(--dur-base) var(--ease-standard);
}
.composer-plus--open {
  background: var(--fill);
  color: var(--ink);
}
.composer-plus--open :deep(.v-icon) {
  transform: rotate(45deg);
}
/* 按住说话时长按不该弹出系统菜单、选中文字。 */
.composer-mic {
  touch-action: none;
  user-select: none;
  -webkit-touch-callout: none;
}

/* 录音条的位置：左边齐这一行，右边齐麦克风的右边缘（让出头像和发送）。 */
.composer-actions__voice {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  right: calc(28px + var(--gap));
  pointer-events: none;
}
.composer-actions--summon .composer-actions__voice {
  right: calc(2 * (28px + var(--gap)));
}
.composer-actions__voice > :deep(*) {
  pointer-events: auto;
}

/* 「还原原话」：安静的一颗小药丸，从左边一点点滑进来，走的时候快一档（§9.3）。
   底下那条细线匀速走完 8 秒，告诉人它待多久。 */
.voice-swap {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  margin-left: 4px;
  padding: 0 8px;
  border: none;
  border-radius: var(--radius-pill);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.voice-swap:hover {
  background: var(--fill-2);
  color: var(--ink);
}
.voice-swap__left {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 2px;
  height: 1.5px;
  border-radius: 1px;
  background: var(--faint);
  opacity: 0.6;
  transform-origin: left;
  animation: voice-swap-left 8s linear both;
}
@keyframes voice-swap-left {
  to {
    transform: scaleX(0);
  }
}
.voice-swap-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.voice-swap-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.voice-swap-enter-from,
.voice-swap-leave-to {
  opacity: 0;
  transform: translateX(-4px);
}

/* 能发了，灰色的键过渡成琥珀；按下去沉一下，不等松手。 */
.composer-send {
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard),
    opacity var(--dur-quick) var(--ease-standard),
    transform var(--dur-press) var(--ease-standard);
}
.composer-send:active:not(:disabled) {
  transform: scale(0.92);
}
/* 发送、✓、转圈叠在同一个位置，换的时候交叉淡化。 */
.composer-send__face {
  display: grid;
  place-items: center;
}
.composer-send__face > * {
  grid-area: 1 / 1;
  transition:
    opacity var(--dur-quick) var(--ease-standard),
    transform var(--dur-quick) var(--ease-standard);
}
.composer-send__icon--off {
  opacity: 0;
  transform: scale(0.7);
}
.composer-send__spin {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: composer-send-spin 0.8s linear infinite;
}
@keyframes composer-send-spin {
  to {
    rotate: 360deg;
  }
}

/* 触屏上手指点得中（设计系统 §10.1）：几颗按钮画出来的样子不变，能点的范围撑到
   44×44（同 style.css 的 .tap-target）。撑开的范围不能互相盖住，所以按钮之间拉开到
   16px；这一行也长到 44px，不让它伸进上面的输入框。 */
.composer-actions--touch {
  --gap: 16px;
}
@media (pointer: coarse) {
  .composer-actions {
    min-height: 44px;
  }
  .composer-icon::before,
  .composer-send::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
/* 关掉动画之后意思还在：细线停在满格，转圈换成静止的半圈。 */
@media (prefers-reduced-motion: reduce) {
  .voice-swap__left,
  .composer-send__spin {
    animation: none;
  }
}
</style>
