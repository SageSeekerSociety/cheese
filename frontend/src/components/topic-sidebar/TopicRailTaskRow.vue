<script setup lang="ts">
// 侧栏里挂在频道下面的一个任务。比频道的字往里缩一点，左边一条竖线从频道的 # 下面
// 一直连到「展开其余」那一行，说「这几条是那个频道里的」；字和频道一样大。
//
// 行尾是那颗灯（`light`，父级按 `lib/roomLight.ts` 算好）：哪几盏、怎么排先后、
// 红灯说的是哪件事都在那一份里，这里只负责把它画出来。灯上只画一盏，蓝垫在状态那几
// 盏下面——「这条现在怎么了」比「这条归谁」先要知道。不亮就不画。
//
// 这一条不是和我有关的、是频道里别人的、被借来凑够三条的（`task.filling_in`）：整个
// 标题淡一档，鼠标停上去的那句说明写清它为什么在这儿 —— 不然侧栏里突然多出一条我没
// 参与过的活，看起来像我漏看了什么。
import type { RowLight } from '@/lib/roomLight'
import type { RailTask } from './moreTasks'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import TopicRailLight from './TopicRailLight.vue'

import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = withDefaults(
  defineProps<{
    task: RailTask
    selected: boolean
    /** 这一行此刻画哪盏灯（父级带钟算的）；不亮是 null。 */
    light?: RowLight | null
    /** 任务里别人说了几句我还没读（只对负责人和协作者算，人和 AI 都算）。 */
    unread?: number
    // 它的频道在树里的第几层：竖线跟着频道的图标列走。
    depth?: number
  }>(),
  { depth: 0, unread: 0, light: null }
)

const fillingIn = computed(() => props.task.filling_in === true)

const emit = defineEmits<{
  (e: 'select', task: { roomId: string; taskId: string }): void
  // 鼠标左键按下去了：意图定了，不必再等指针停满 hover 那一百多毫秒（和频道行
  // `TopicRailRow` 的 `press` 同一条理由）。任务页的代码是懒加载的，早下这一百多
  // 毫秒，打开它就少等这一段。
  (e: 'press', task: { roomId: string; taskId: string }): void
}>()

// 点下去到任务页出来之间，这一行一直亮着：只靠按下的那一下，手指抬起后又变回原样，
// 等得久的人会以为没点到（同 TopicRailRow）。
const pending = ref(false)
let pendingTimer: number | undefined
function choose(): void {
  // 点的就是当前这一行：不跳转，也就没有「等它选中」这回事，别亮着不放。
  if (props.selected) {
    emit('select', { roomId: props.task.room_id, taskId: props.task.id })
    return
  }
  pending.value = true
  window.clearTimeout(pendingTimer)
  pendingTimer = window.setTimeout(() => (pending.value = false), 10_000)
  emit('select', { roomId: props.task.room_id, taskId: props.task.id })
}
watch(
  () => props.selected,
  (on) => {
    if (!on) return
    pending.value = false
    window.clearTimeout(pendingTimer)
  }
)
onBeforeUnmount(() => window.clearTimeout(pendingTimer))
</script>

<template>
  <button
    type="button"
    class="rail-task"
    :class="{ 'rail-task--selected': selected, 'rail-task--filled': fillingIn, 'rail-task--pending': pending }"
    :style="{ '--task-indent': 40 + depth * 20 + 'px', '--guide-x': 16 + depth * 20 + 'px' }"
    :aria-current="selected ? 'page' : undefined"
    :title="fillingIn ? t('work.sidebar.fillInTip') : undefined"
    @click="choose"
    @pointerdown="
      $event.pointerType === 'mouse' && $event.button === 0 && emit('press', { roomId: task.room_id, taskId: task.id })
    "
  >
    <!-- 任务名是人起的（占位名除外，那是界面上的字）。 -->
    <span
      class="rail-task__title"
      :class="{ 'rail-task__title--unread': unread > 0 }"
      :data-user-content="task.title_source === 'placeholder' ? undefined : ''"
      >{{ taskTitle(task) }}</span
    >
    <TopicRailLight v-if="light" :light="light" />
  </button>
</template>

<style scoped>
.rail-task {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding-block: 0;
  padding-inline-start: calc(var(--task-indent) + 20px);
  padding-inline-end: 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-family: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-task:hover {
  background: var(--fill-2);
  color: var(--ink);
}
/* 按下那一帧就换底色：触屏没有 hover，任务页又要等一下才出来。 */
.rail-task:active {
  background: var(--line-2);
  color: var(--ink);
}
/* 手指抬起之后到任务页出来之前，这一行保持亮着。 */
.rail-task--pending {
  background: var(--line-2);
  color: var(--ink);
}
/* 选中沿用频道行那一档：底色 --line-2，标题再重一层。这条 rail 的底是 --canvas，
   --fill 在它上面只有 1.027:1，拿它当选中等于没画（同 TopicRailRow.vue 那段注释）。
   字重落在行上、由标题继承。选中的行不复用 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.rail-task--selected,
.rail-task--selected:hover {
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 竖线落在频道 # 的中线上，一行接一行连成一条（上下各探 3px 盖住行间的空隙）。
   结构线，不是强调条。 */
.rail-task::before {
  content: '';
  position: absolute;
  left: var(--guide-x);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}
.rail-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 未读只给名称加粗，不再另挂数字。 */
.rail-task__title--unread {
  font-weight: 650;
}
/* 借来凑数的那几条退到次要色：它们不是我的活，只是把频道里的三行凑齐。选中和 hover 要
   自己再抬一次——上面那两条改的是行盒的颜色，标题自己有颜色时继承不到。 */
.rail-task--filled .rail-task__title {
  color: var(--muted);
}
.rail-task--filled:hover .rail-task__title,
.rail-task--filled.rail-task--selected .rail-task__title {
  color: var(--ink);
}
.topic-rail--page .rail-task {
  padding-inline-start: calc(var(--task-indent) + 76px);
}
.topic-rail--page .rail-task::before {
  left: calc(var(--guide-x) + 14px);
}
@media (hover: none) {
  .rail-task {
    padding-inline-start: calc(var(--task-indent) + 76px);
  }
  .rail-task::before {
    left: calc(var(--guide-x) + 14px);
  }
}
</style>
