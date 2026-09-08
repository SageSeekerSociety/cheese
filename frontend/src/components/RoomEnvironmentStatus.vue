<script setup lang="ts">
// 这个房间的运行环境还没准备好 —— 也就是「你现在做什么都不会有反应」。
//
// 它是**房间级**的状态，所以住在话题标题下面，四格（对话/改动/现场/预览）里都看
// 得见。它一度被挪进对话栏的输入框上沿，理由是「这句话是关于你要发的那条消息
// 的」——那个读法是错的：环境没起来时改动、现场、预览同样全是空的，人可能正在
// 任何一格里等，而那时候屏幕上什么都不说。
//
// 但也不放回标题**上面**（它最早在那儿）：一个会消失的临时状态不该把常驻的标题
// 挤下去。标题之下、四格之上，是这两条约束唯一的交点。
//
// 也不进时间线：它是状态不是消息。会消失的消息让人怀疑自己看错了，不消失的
// 「正在准备」第二天就是假话，而且后面两条消息就能把它顶走。
import type { EnvironmentStatus } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getRoomEnvironment } from '../api'

const props = defineProps<{ projectId: string; topicId: string }>()
const status = ref<EnvironmentStatus | null>(null)
const logOpen = ref(false)
/** 连着几次没拉到状态。 */
const misses = ref(0)
/** 拉不到状态要连错两次才说话。
 *
 *  一次就报会让一下网络抖动闪出一条错；而一直不说，则是这个组件最坏的失败方式
 *  —— 它的职责就是解释「芝士为什么没反应」，平台整个挂掉的时候它反而第一个安静
 *  下来（那正是 2026-09-08 那次 502 的现场：界面上一个字都没有）。
 *
 *  两次听起来很松，其实不是：`api.ts` 对 GET 的 502 自己还会退避重试两次，所以
 *  这里的一次「失败」已经是三次请求、一秒多。连着两次 ≈ 12 秒、六次请求 —— 穿得
 *  过抖动，短过一个人开始怀疑自己。 */
const MISS_LIMIT = 2
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

watch(
  () => [props.projectId, props.topicId],
  () => {
    const current = ++generation
    clearTimeout(timer)
    status.value = null
    misses.value = 0
    logOpen.value = false
    async function refresh() {
      try {
        const result = await getRoomEnvironment(props.projectId, props.topicId)
        if (current === generation) {
          status.value = result
          misses.value = 0
        }
      } catch {
        // 不清 status：连错两次之前，屏幕上留着上一次读到的样子，比闪成空白诚实。
        if (current === generation) misses.value += 1
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

const st = computed(() => status.value?.state)
const retrying = computed(() => status.value?.recovery_state === 'retrying')

/** 「机器还在往前走，等着就行」的那几种。
 *
 *  `pending` 以前不画，而它恰恰是一个**新话题的起始状态**（房间还没绑设备，或者
 *  机器上还没铺好环境脚本）——于是最需要解释的那几十秒里，屏幕上一个字都没有。 */
const working = computed(() => st.value === 'pending' || st.value === 'preparing' || retrying.value)

/** 「停在这儿了，要人管」的那几种。
 *
 *  `offline` 以前也不画，而它比失败更需要说出来：绑的那台机器不在线，芝士收不到
 *  任何消息，**而且这不会自己好** —— 没有人去连机器，这个房间就永远这样。 */
const stuck = computed(() => (st.value === 'failed' || st.value === 'offline') && !retrying.value)

/** 连着拉不到状态。读不到本身就是信息：它多半意味着平台这会儿不正常。 */
const unreachable = computed(() => misses.value >= MISS_LIMIT)

const shown = computed(() => unreachable.value || working.value || stuck.value)
/** 只有「在往前走」才画那条动的线；停住了和读不到都不该还有东西在动。 */
const moving = computed(() => !unreachable.value && working.value)

// `stopped` 不在上面任何一组里：它由设备端脚本返回，语义（是人停的？还是崩了？）
// 我没核实，宁可继续不画，也不猜一句话贴到用户脸上。要补它得先去看
// cheese-environment.py 到底什么时候返这个值。

/** 主句。 */
const line = computed(() => {
  if (unreachable.value) return '暂时读不到运行环境状态'
  if (st.value === 'pending') return '正在准备运行环境，完成后芝士会继续处理你的消息'
  if (st.value === 'preparing') {
    return status.value?.stage === 'setup'
      ? '正在安装工具，完成后芝士会继续处理你的消息'
      : '正在准备项目，完成后芝士会继续处理你的消息'
  }
  if (retrying.value) return '总览芝士已修正环境配置，正在重新启动'
  if (st.value === 'offline') return '运行设备已离线，芝士接不到你的消息'
  return '环境准备失败，芝士还没有开始处理这条消息'
})

/** 停住时的下一步。一句，跟在主句后面的小字。 */
const nextStep = computed(() => {
  if (unreachable.value) return '这多半是平台正忙或暂时不可用，会自己重试'
  if (!stuck.value) return ''
  if (st.value === 'offline') return '请重新连接这台设备，或在运行环境设置中换一台'
  if (status.value?.recovery_state === 'requested') return '已交给总览芝士检查，可在总览查看处理情况'
  if (status.value?.recovery_state === 'needs_help') return '自动处理未能恢复环境，请在总览查看需要的协助'
  return '请查看安装日志，或在运行环境设置中修改配置并安排重试'
})
</script>

<template>
  <div v-if="shown" class="env" :class="{ 'env--stuck': stuck, 'env--unreachable': unreachable }" role="status">
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
    <span v-if="moving" class="env__bar" aria-hidden="true" />
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
.env--stuck {
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
.env--stuck .env__dot {
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
.env--stuck .env__line {
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
.env--stuck .env__toggle:hover {
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

/* 读不到状态：中性，比「停住了」轻一档。它说的不是这个房间坏了，是我们这会儿看
   不清 —— 把它画成红的会让人去修一个可能根本不存在的问题。 */
.env--unreachable .env__dot {
  background: var(--faint);
}
.env--unreachable .env__next {
  color: var(--muted);
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
