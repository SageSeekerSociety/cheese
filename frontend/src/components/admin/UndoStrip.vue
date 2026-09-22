<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

/**
 * UndoStrip.vue — 分诊写下去之后那条 32px 的撤销条。
 *
 * 分诊是单键操作（`1`/`2`/`3`），误触的代价必须是「再按一次 `U`」，而不是去翻列表找
 * 刚才那条 —— 这条横幅存在的全部理由就是这个，所以它**自己会走**（5s），栈深 1：
 * 第二次分诊把上一条顶掉，不是排队。
 *
 * 计时器分两只，这是这个组件唯一不显然的地方：`dismiss` 只能交给父组件去撤（组件
 * 没资格在自己的树里把自己删掉），而父组件收到 `dismiss` 通常是**直接卸载** —— 那时
 * 淡出根本没机会播。所以 5s 到点之前 0.2s 就开始淡，5s 那一刻淡完、同时 emit：
 * 「5s 后消失」这条验收（5.0s ± 0.3s）在两个方向上都不吃亏。
 */
defineOptions({ name: 'UndoStrip' })

const props = withDefaults(
  defineProps<{
    /** 已拼好的整句（`feedback.undo.message`），这里不再拼文案：状态名归调用方。 */
    message: string
    /** 停留多久。测试与「连按两下」的脚本要能把它调小。 */
    timeoutMs?: number
  }>(),
  { timeoutMs: 5000 }
)

const emit = defineEmits<{ (e: 'undo'): void; (e: 'dismiss'): void }>()

const { t } = useI18n()

/** 和 `.ustrip-enter-active` / `.ustrip-leave-active` 里的 0.2s 是同一件事，一起改。 */
const FADE_MS = 200

const shown = ref(true)
let fadeTimer: ReturnType<typeof setTimeout> | undefined
let dismissTimer: ReturnType<typeof setTimeout> | undefined

function clearTimers() {
  clearTimeout(fadeTimer)
  clearTimeout(dismissTimer)
  fadeTimer = undefined
  dismissTimer = undefined
}

function arm() {
  clearTimers()
  // 小到 200ms 以下的 timeout（测试会这么调）不能让「提前 0.2s 开始淡」变成负数。
  const fadeAt = Math.max(0, props.timeoutMs - FADE_MS)
  fadeTimer = setTimeout(() => {
    shown.value = false
  }, fadeAt)
  dismissTimer = setTimeout(() => emit('dismiss'), props.timeoutMs)
}

arm()

// 同一个实例被复用（父组件没换 key）时，`message` 变了就是**换了一条撤销**。旧计时器
// 必须作废：不然上一条的 5s 会把这一条提前收走，用户看到的是「按了 1，提示闪一下没了」，
// 而这条 bug 只在「两次分诊间隔 < 5s」时出现 —— 恰恰是最常见的用法。
watch([() => props.message, () => props.timeoutMs], () => {
  shown.value = true
  arm()
})

onBeforeUnmount(clearTimers)
</script>

<template>
  <Transition name="ustrip" appear>
    <div v-if="shown" class="ustrip" role="status" aria-live="polite">
      <span class="ustrip__msg">{{ props.message }}</span>
      <button type="button" class="ustrip__undo" @click="emit('undo')">{{ t('feedback.undo.action') }}</button>
      <!-- 关闭是纯图标，名字只能挂在 aria-label 上；管理后台这几个页面里用户可见的
           字都是直接写中文的（见 AdminMembersPage 的「刷新」），这里跟着它们。 -->
      <button type="button" class="ustrip__close" aria-label="关闭" @click="emit('dismiss')">
        <v-icon icon="mdi-close" size="16" />
      </button>
    </div>
  </Transition>
</template>

<style scoped>
/* 固定在视口底部 16px、水平居中。用 fixed 而不是 absolute：这条横幅由队列页渲染，
   而队列页的滚动容器长什么样是那一线的事 —— 钉在视口上，挂在哪棵 DOM 里都对。 */
.ustrip {
  display: flex;
  position: fixed;
  bottom: 16px;
  left: 50%;
  z-index: 30;
  align-items: center;
  box-sizing: border-box;
  gap: 12px;
  width: max-content;
  height: 32px;
  max-width: calc(100vw - 32px);
  min-width: 320px;
  padding: 0 8px 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--surface);
  background: var(--ink);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  transform: translateX(-50%);
}

.ustrip__msg {
  white-space: nowrap;
}

/* 「撤销」是这条横幅上唯一的主操作，做成描边小按钮；hover 时整块反色（只改颜色，
   不动位置），让它在深底上明确是个可点的东西。 */
.ustrip__undo {
  flex: 0 0 auto;
  height: 20px;
  padding: 0 8px;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  color: var(--surface);
  cursor: pointer;
  background: transparent;
  border: 1px solid currentcolor;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.ustrip__undo:hover {
  color: var(--ink);
  background: var(--surface);
}

.ustrip__close {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--surface);
  cursor: pointer;
  background: transparent;
  border: 0;
  opacity: 0.7;
}

.ustrip__close:hover {
  opacity: 1;
}

/* 出现 / 消失各 0.2s（规格 §7.7 那一档）。`transform` 里必须把居中的 translateX
   一起写上：只写 translateY 会让它先跳到屏幕左边再升上来。 */
.ustrip-enter-active,
.ustrip-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s ease;
}

.ustrip-enter-from,
.ustrip-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}

.ustrip-enter-to,
.ustrip-leave-from {
  transform: translateX(-50%);
}
</style>
