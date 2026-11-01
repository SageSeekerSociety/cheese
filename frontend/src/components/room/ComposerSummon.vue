<script setup lang="ts">
// 发送键左边那颗 AI 队友头像：这条发出去，谁会接手。
//
// 它不存状态，是正文的镜子：正文里 @ 了哪位队友，头像就是哪位、睁着眼；没 @ 任何一位，
// 头像灰着、闭着眼，画的是点一下会交给的那一位。点一下、⌘/Ctrl+Enter、手打 @ 写的都是
// 正文里同一枚标签（RoomComposer 的 `toggleSummon`）。
//
// 换一位：右键、长按、焦点在它上面时按 ↑，弹出「交给哪位队友」。只有一位队友时不弹——
// 一行的选单比没有选单还多一步。打 @ 选人是同一件事的另一条路，所以长按只是快捷方式
// （设计系统 §10.6）。
//
// 任务负责人那里每句都交给队友，这是规则不是开关：头像常亮，点了不改正文。
//
// 表情只说状态：闭眼（不交给）、悬停时眯眼（点一下它就醒了）、睁眼（交给它）、往右上看
// 冒泡（它正在这个频道里跑一轮，这条它会接着看）。闭眼那一种在头像左边再写一句
// 「不交给 AI」：只有一颗灰着的头像，容易被当成装饰读过去。三种状态是一个值（`face`），
// 那句字和悬停提示都读它，不会一个说不交给、一个说它会接着看。
import { computed, onBeforeUnmount, ref } from 'vue'
import { useDisplay } from 'vuetify'
import { useEventListener } from '@vueuse/core'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import { t } from '@/i18n'

interface SummonMate {
  handle: string
  label: string
}

const props = defineProps<{
  /** 这个频道里能交给的 AI 队友。第一位是频道自己的那位。 */
  mates: SummonMate[]
  /** 头像上画的那几位：正文里 @ 到的，没有就是点一下会交给的那一位。最多叠两位。 */
  shown: SummonMate[]
  /** 这条消息会不会交给队友（读的是正文）。 */
  on: boolean
  /** 规则常亮（任务负责人）：点了只说明，不改正文。 */
  fixed: boolean
  /** 名册还没到：写进去的 @ 解析不成 handle，这一刻不接受点击。 */
  ready: boolean
  /** 正在这个频道里跑一轮的队友。 */
  busy: string[]
  /** 只 @ 了人、没 @ 队友：任务里这一句不交给队友。 */
  peopleOnly?: boolean
  guided?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'pick', handle: string): void
  (e: 'off'): void
}>()

const { mdAndUp } = useDisplay()
// 触屏上换一位是长按，不是右键：提示按输入方式说（和 ComposerActions 同一条判据）。
const touchQuery = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
const touchOnly = ref(!!touchQuery?.matches)
useEventListener(touchQuery, 'change', (e: MediaQueryListEvent) => (touchOnly.value = e.matches))
const lead = computed(() => props.shown[0] ?? null)
const canPick = computed(() => !props.fixed && props.ready && props.mates.length > 1)
const isBusy = computed(() => !!lead.value && props.busy.includes(lead.value.handle))
/** 这条发出去：交给它（on）、它正在跑一轮会接着看（busy）、谁也不交（off）。 */
const face = computed<'on' | 'busy' | 'off'>(() => (props.on ? 'on' : isBusy.value ? 'busy' : 'off'))
const names = computed(() => props.shown.map((m) => m.label).join(t('work.room.composer.summonJoin')))

const tip = computed(() => {
  const name = lead.value?.label ?? ''
  if (props.fixed)
    return props.on ? t('work.room.composer.summonRule', { name }) : t('work.room.composer.summonPeopleOnly')
  if (face.value === 'on')
    return props.shown.length > 1
      ? t('work.room.composer.summonOnMany', { names: names.value })
      : t('work.room.composer.summonOn', { name })
  if (face.value === 'busy') return t('work.room.composer.summonBusy', { name })
  if (!canPick.value) return t('work.room.composer.summonOff', { name })
  return touchOnly.value
    ? t('work.room.composer.summonOffManyTouch', { name })
    : t('work.room.composer.summonOffMany', { name })
})

// ---- 换一位 ----
const pickerOpen = ref(false)
function openPicker() {
  if (canPick.value) pickerOpen.value = true
}
function pick(handle: string) {
  pickerOpen.value = false
  emit('pick', handle)
}
function off() {
  pickerOpen.value = false
  emit('off')
}

// 长按：0.45 秒到了就打开选单，这一次松手不算点击。按下时头像慢慢缩一点，告诉人「按着呢」。
const LONG_MS = 450
let longTimer = 0
let longFired = false
const pressing = ref(false)
function onDown(e: PointerEvent) {
  longFired = false
  if (e.button !== 0 || !canPick.value) return
  pressing.value = true
  window.clearTimeout(longTimer)
  longTimer = window.setTimeout(() => {
    longFired = true
    pressing.value = false
    openPicker()
  }, LONG_MS)
}
function onUp() {
  window.clearTimeout(longTimer)
  pressing.value = false
}
onBeforeUnmount(() => window.clearTimeout(longTimer))

function onClick() {
  if (longFired) {
    longFired = false
    return
  }
  if (!props.ready || props.fixed) return
  emit('toggle')
}
// 右键：桌面上换一位。安卓上长按也会发这一下，和上面的计时器是同一件事，只开一次。
function onContext(e: MouseEvent) {
  if (!canPick.value) return
  e.preventDefault()
  window.clearTimeout(longTimer)
  // 手指还按着（安卓长按发的这一下）：松手时那一下 click 不算点击。鼠标右键后面不跟 click，
  // 这里要是也记上，下一次真的点头像就会被吞掉。
  if (pressing.value) longFired = true
  pressing.value = false
  openPicker()
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowUp' || e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
    if (!canPick.value) return
    e.preventDefault()
    openPicker()
  }
}

const rows = computed(() =>
  props.mates.map((m) => ({
    ...m,
    current: props.on && props.shown.some((s) => s.handle === m.handle),
    busy: props.busy.includes(m.handle),
  }))
)
</script>

<template>
  <span class="summon">
    <span v-if="face === 'off'" class="summon__off">{{ t('work.room.composer.summonOffLabel') }}</span>
    <button
      v-guide-anchor="guided ? 'composer-summon' : ''"
      type="button"
      class="summon__btn"
      :class="{
        'summon__btn--on': on,
        'summon__btn--fixed': fixed,
        'summon__btn--busy': isBusy,
        'summon__btn--pressing': pressing,
        'summon__btn--menu': pickerOpen,
      }"
      :disabled="!ready"
      :aria-pressed="fixed ? undefined : on"
      :aria-haspopup="canPick ? 'menu' : undefined"
      :aria-expanded="canPick ? pickerOpen : undefined"
      :aria-label="tip"
      :title="tip"
      @click="onClick"
      @contextmenu="onContext"
      @pointerdown="onDown"
      @pointerup="onUp"
      @pointerleave="onUp"
      @pointercancel="onUp"
      @keydown="onKey"
    >
      <span class="summon__face">
        <!-- 换人：旧的往上退、新的从下面升上来，像换了一个人值班。 -->
        <Transition name="summon-swap">
          <span v-if="shown.length > 1" key="stack" class="summon__stack">
            <CheeseAvatar :size="20" :name="shown[0].label" :handle="shown[0].handle" />
            <CheeseAvatar :size="20" :name="shown[1].label" :handle="shown[1].handle" class="summon__stack-2" />
          </span>
          <CheeseAvatar
            v-else-if="lead"
            :key="lead.handle"
            :size="24"
            :name="lead.label"
            :handle="lead.handle"
            :state="isBusy ? 'think' : null"
          />
        </Transition>
      </span>
    </button>

    <!-- 换一位：桌面上从头像上方弹出，手机上从底部升起。 -->
    <v-menu
      v-if="mdAndUp && canPick"
      v-model="pickerOpen"
      activator="parent"
      :open-on-click="false"
      location="top end"
      :offset="8"
    >
      <v-list class="summon-menu" role="menu" :aria-label="t('work.room.composer.summonPicker')">
        <div class="summon-menu__head t-meta-read">{{ t('work.room.composer.summonPicker') }}</div>
        <v-list-item
          v-for="row in rows"
          :key="row.handle"
          role="menuitemradio"
          :aria-checked="row.current"
          :active="row.current"
          class="summon-menu__row"
          @click="pick(row.handle)"
        >
          <template #prepend><CheeseAvatar :size="28" :name="row.label" :handle="row.handle" /></template>
          <v-list-item-title>{{ row.label }}</v-list-item-title>
          <v-list-item-subtitle v-if="row.busy">{{ t('work.room.composer.summonPickerBusy') }}</v-list-item-subtitle>
          <template v-if="row.current" #append
            ><v-icon size="16" class="summon-menu__check">mdi-check</v-icon></template
          >
        </v-list-item>
        <template v-if="on">
          <v-divider class="summon-menu__divider" />
          <v-list-item role="menuitem" prepend-icon="mdi-close" @click="off">
            <v-list-item-title>{{ t('work.room.composer.summonPickerOff') }}</v-list-item-title>
          </v-list-item>
        </template>
      </v-list>
    </v-menu>
    <MobileActionSheet v-else-if="canPick" v-model="pickerOpen" :title="t('work.room.composer.summonPicker')">
      <ul class="summon-sheet" role="menu">
        <li v-for="row in rows" :key="row.handle" role="none">
          <button
            type="button"
            role="menuitemradio"
            :aria-checked="row.current"
            class="summon-sheet__row"
            :class="{ 'summon-sheet__row--current': row.current }"
            @click="pick(row.handle)"
          >
            <CheeseAvatar :size="32" :name="row.label" :handle="row.handle" />
            <span class="summon-sheet__text">
              <span class="summon-sheet__name">{{ row.label }}</span>
              <span v-if="row.busy" class="summon-sheet__sub">{{ t('work.room.composer.summonPickerBusy') }}</span>
            </span>
            <v-icon v-if="row.current" size="20" class="summon-sheet__check">mdi-check</v-icon>
          </button>
        </li>
        <li v-if="on" role="none" class="summon-sheet__apart">
          <button type="button" role="menuitem" class="summon-sheet__row" @click="off">
            <span class="summon-sheet__icon"><v-icon size="20">mdi-close</v-icon></span>
            <span class="summon-sheet__name">{{ t('work.room.composer.summonPickerOff') }}</span>
          </button>
        </li>
      </ul>
    </MobileActionSheet>
  </span>
</template>

<style scoped>
.summon {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
/* 「不交给 AI」：和输入框周围别的状态字一样安静（13px、--muted），不抢发送键。录音时
   录音条从麦克风往左展开，盖住这一句。 */
.summon__off {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}
.summon__btn {
  position: relative;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  touch-action: manipulation;
  -webkit-touch-callout: none;
  user-select: none;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.summon__btn:hover:not(:disabled),
.summon__btn--menu {
  background: var(--fill);
}
.summon__btn--fixed {
  cursor: default;
}
.summon__btn--fixed:hover:not(:disabled) {
  background: transparent;
}
.summon__btn:disabled {
  cursor: default;
  opacity: 0.5;
}
.summon__face {
  display: grid;
  place-items: center;
  transition: transform var(--dur-press) var(--ease-standard);
}
/* 按着：头像慢慢缩一点，到时间就弹出选单。 */
.summon__btn--pressing .summon__face {
  transform: scale(0.9);
  transition: transform 450ms var(--ease-standard);
}
.summon__face > * {
  grid-area: 1 / 1;
}

/* 不交给：灰底、闭眼。底色和眼睛都是过渡：交给它时底色先到，眼睛晚 40ms 睁开。 */
.summon__btn :deep(.cheese-avatar__tile) {
  transition: fill var(--dur-base) var(--ease-standard);
}
.summon__btn :deep(.cheese-avatar__eye) {
  transition:
    transform var(--dur-base) var(--ease-out) 40ms,
    fill var(--dur-base) var(--ease-standard);
}
.summon__btn:not(.summon__btn--on) :deep(.cheese-avatar__tile) {
  fill: var(--line-2);
}
.summon__btn:not(.summon__btn--on) :deep(.cheese-avatar__eye) {
  fill: var(--faint);
  transform: scaleY(0.16);
  animation: none;
  transition:
    transform var(--dur-quick) var(--ease-in),
    fill var(--dur-quick) var(--ease-standard);
}
.summon__btn:not(.summon__btn--on) :deep(.cheese-avatar__bubble) {
  fill: var(--faint);
}
/* 悬停眯眼：预告点一下它就醒了。 */
.summon__btn:hover:not(.summon__btn--on, .summon__btn--fixed, :disabled) :deep(.cheese-avatar__eye) {
  transform: scaleY(0.5);
  transition: transform var(--dur-base) var(--ease-out);
}

.summon__stack {
  position: relative;
  display: block;
  width: 30px;
  height: 20px;
}
.summon__stack > :deep(.cheese-avatar) {
  position: absolute;
  top: 0;
  left: 0;
}
.summon__stack > :deep(.summon__stack-2) {
  left: 10px;
  filter: drop-shadow(-1.5px 0 0 var(--surface));
}

.summon-swap-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.summon-swap-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.summon-swap-enter-from {
  opacity: 0;
  transform: translateY(7px);
}
.summon-swap-leave-to {
  opacity: 0;
  transform: translateY(-7px);
}

.summon-menu {
  min-width: 260px;
  padding: 4px;
}
.summon-menu__head {
  padding: 4px 12px 6px;
}
.summon-menu__row :deep(.v-list-item__prepend) {
  margin-inline-end: 10px;
}
.summon-menu__check {
  color: var(--accent-ink);
}
.summon-menu__divider {
  margin: 4px 0;
}

.summon-sheet {
  margin: 0;
  padding: 0 0 8px;
  list-style: none;
}
.summon-sheet__row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 56px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.summon-sheet__row:active,
.summon-sheet__row--current {
  background: var(--fill);
}
.summon-sheet__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.summon-sheet__name {
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--ink);
}
.summon-sheet__sub {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.summon-sheet__check {
  margin-left: auto;
  color: var(--accent-ink);
}
.summon-sheet__icon {
  display: grid;
  place-items: center;
  width: 32px;
  color: var(--muted);
}
.summon-sheet__apart {
  margin-top: 4px;
  border-top: 1px solid var(--line);
}

@media (pointer: coarse) {
  .summon__btn::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .summon__btn--pressing .summon__face {
    transform: none;
  }
}
</style>
