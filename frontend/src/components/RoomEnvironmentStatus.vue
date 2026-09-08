<script setup lang="ts">
// 这个房间的运行环境还没准备好 —— 说的是「你现在打的这条，芝士还接不到」。
//
// 所以它住在**输入框上沿**（ChatPanel 的 composer-notice slot），不在页顶也不在
// 时间线里：摆进时间线会被后面的消息顶走，而它最该被看见的时刻正是人在打字的那
// 一刻；摆到页顶则会横跨工作面板、还把话题标题挤下去，可改动/现场/预览那半边跟
// 它没有关系。
//
// 它是**状态**不是消息：会自己消失（准备好了就没了），所以不能是时间线上的一
// 条——一条会消失的消息让人怀疑自己看错了，一条不消失的「正在准备」第二天就是
// 假话。
import type { EnvironmentStatus } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getRoomEnvironment } from '../api'

const props = defineProps<{ projectId: string; topicId: string }>()
const status = ref<EnvironmentStatus | null>(null)
const logOpen = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

watch(
  () => [props.projectId, props.topicId],
  () => {
    const current = ++generation
    clearTimeout(timer)
    status.value = null
    logOpen.value = false
    async function refresh() {
      try {
        const result = await getRoomEnvironment(props.projectId, props.topicId)
        if (current === generation) status.value = result
      } catch {
        // The room remains usable while its machine status is unavailable.
        if (current === generation) status.value = null
      } finally {
        if (current === generation) timer = setTimeout(refresh, 5000)
      }
    }
    void refresh()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  generation++
  clearTimeout(timer)
})

const preparing = computed(() => status.value?.state === 'preparing')
const retrying = computed(() => status.value?.recovery_state === 'retrying')
const failed = computed(() => status.value?.state === 'failed' && !retrying.value)
const shown = computed(() => preparing.value || retrying.value || failed.value)

/** 主句。准备中和重启中都是「在进行」，失败是「停住了」。 */
const line = computed(() => {
  if (preparing.value) {
    return status.value?.stage === 'setup'
      ? '正在安装工具，完成后芝士会继续处理你的消息'
      : '正在准备项目，完成后芝士会继续处理你的消息'
  }
  if (retrying.value) return '总览芝士已修正环境配置，正在重新启动'
  return '环境准备失败，芝士还没有开始处理这条消息'
})

/** 失败时的下一步。一句，跟在主句后面的小字。 */
const nextStep = computed(() => {
  if (!failed.value) return ''
  if (status.value?.recovery_state === 'requested') return '已交给总览芝士检查，可在总览查看处理情况'
  if (status.value?.recovery_state === 'needs_help') return '自动处理未能恢复环境，请在总览查看需要的协助'
  return '请查看安装日志，或在运行环境设置中修改配置并安排重试'
})
</script>

<template>
  <div v-if="shown" class="env" :class="{ 'env--failed': failed }" role="status">
    <div class="env__row">
      <!-- 记号色只做记号（设计系统 §1.5）：这一颗是点，字用的是 -ink 那一档。 -->
      <span class="env__dot" aria-hidden="true" />
      <span class="env__line">{{ line }}</span>
      <button v-if="status?.log" type="button" class="env__toggle" :aria-expanded="logOpen" @click="logOpen = !logOpen">
        {{ logOpen ? '收起日志' : '安装日志' }}
      </button>
    </div>
    <p v-if="nextStep" class="env__next">{{ nextStep }}</p>
    <pre v-if="logOpen && status?.log" class="env__log">{{ status.log }}</pre>
    <!-- 进行中的那条细线贴在下沿，1px 高。它替掉的是一条横跨整个屏幕的
         v-progress-linear——那东西的信息量只有「还在跑」，却是整屏最吵的一个。 -->
    <span v-if="preparing || retrying" class="env__bar" aria-hidden="true" />
  </div>
</template>

<style scoped>
.env {
  position: relative;
  padding: 7px 10px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
  overflow: hidden;
}
/* 失败要显眼：这是唯一需要人去做点什么的状态。 */
.env--failed {
  border-color: var(--danger);
  background: var(--danger-wash);
}
.env__row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.env__dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}
.env--failed .env__dot {
  background: var(--danger);
}
.env__line {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted);
}
.env--failed .env__line {
  color: var(--danger-ink);
}
.env__toggle {
  flex: none;
  margin-left: auto;
  padding: 1px 7px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.env__toggle:hover {
  background: var(--fill-2);
  color: var(--text);
}
.env--failed .env__toggle:hover {
  background: var(--surface);
}
.env__next {
  margin: 3px 0 0 13px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--danger-ink);
}
.env__log {
  max-height: 200px;
  margin: 6px 0 0;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}

/* 进行中：一条 1px 的线在下沿来回扫。 */
.env__bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1px;
  background: var(--line-2);
}
.env__bar::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 32%;
  background: var(--ok);
  animation: env-sweep 1.6s ease-in-out infinite;
}
@keyframes env-sweep {
  0% {
    left: -32%;
  }
  100% {
    left: 100%;
  }
}
/* 全局那条兜底把时长压到 0.001ms，对无限循环等于把扫光变成高频闪烁（设计系统
   §9.5）。这里自己关掉，并让那条线保持整条实心——「在进行」这件事仍然读得出
   来，只是不再动。 */
@media (prefers-reduced-motion: reduce) {
  .env__bar::after {
    animation: none;
    left: 0;
    width: 100%;
  }
}
</style>
