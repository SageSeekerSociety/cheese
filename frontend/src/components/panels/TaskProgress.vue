<script setup lang="ts">
// Task Progress —— 房间总览最上面那一段：这个房间下面有哪些活，各自处在哪一格。
//
// 它取代了原来那个独立的「任务」tab。合并的理由不是省一个 tab：文档和这份清单
// 回答的是同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得
// 先想起来还有另一半。现在文档接在它下面，一屏就是全部。
//
// 每行一条活：状态圆环 +「Task N: 做什么」+ 小字写 subagent 和负责人。圆环显示
// 状态不显示百分比（`lib/taskRing.ts` 说明了为什么没有百分比可显示）。
//
// 「已交付 / 已关闭未交付」分两段列，因为它们不是同一件事：一条已交付的活是这个
// 房间的产出，一条关掉却什么都没交付的活是被放弃的——混在一起看不出这个房间到底
// 交出去了多少。
import type { Block, RoomTask, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { listRoomTasks } from '../../api'
import { roomIdOf } from '../../lib/place'
import { relTime } from '../../lib/relTime'
import { ringRank, taskRing } from '../../lib/taskRing'

const props = withDefaults(
  defineProps<{
    /** 当前打开的地点。是支线时列的仍然是**它所在房间**的活（包括它自己）。 */
    topic: Topic | null
    /** 这一段在屏幕上。折叠起来的时候不去拉。 */
    active?: boolean
    /** 每有一轮动静就加一。 */
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'count', n: number): void
}>()

type ThreadRow = RoomTask & { blocks?: Block[] }

const rows = ref<ThreadRow[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 默认展开：你打开一个房间的第一个问题就是「现在有什么在动」，折起来等于不答。
const open = ref(true)

async function load() {
  const place = props.topic
  if (!place) {
    rows.value = []
    return
  }
  // 活挂在房间上，所以问的永远是房间——在一条支线里打开它，看到的是它的同伴。
  const roomId = roomIdOf(place)
  loading.value = true
  errorMsg.value = null
  try {
    // limit: 1 是必须的，不是优化。不传的话后端会把房间里**每一条**支线的完整
    // 历史都吐回来，而一个跑久了的房间有近两百条活。这里只要每条最新的那一块，
    // 用来说「最后活动」。
    const payload = await listRoomTasks(roomId, { limit: 1 })
    if (props.topic && roomIdOf(props.topic) === roomId) {
      rows.value = payload.data
      emit('count', payload.data.length)
    }
  } catch {
    errorMsg.value = '任务列表加载失败'
  } finally {
    if (props.topic && roomIdOf(props.topic) === roomId) loading.value = false
  }
}

watch(
  () => [props.topic?.id, props.active, props.refreshTick] as const,
  ([, isActive], prev) => {
    const placeChanged = prev?.[0] !== props.topic?.id
    if (placeChanged) rows.value = []
    if (isActive) void load()
  },
  { immediate: true }
)

/** 最后活动 = 这条支线最新那一块的时间；一句话都还没说过的就用它建出来的时间。 */
function lastActivity(row: ThreadRow): string | null {
  return row.blocks?.[row.blocks.length - 1]?.created_at ?? row.updated_at ?? row.created_at ?? null
}

/** 「Task N」的 N —— 按这个房间派活的先后，和界面上怎么排序无关。
 *
 *  编号必须稳定：它是人在对话里指代一条活的方式（「Task 3 卡住了」），跟着排序
 *  变的编号说的是别的活。 */
const numberOf = computed(() => {
  const byBirth = [...rows.value].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  return new Map(byBirth.map((r, i) => [r.id, i + 1]))
})

function sortRows(list: ThreadRow[]): ThreadRow[] {
  return [...list].sort((a, b) => {
    const rank = ringRank(taskRing(a).state) - ringRank(taskRing(b).state)
    if (rank !== 0) return rank
    return Date.parse(lastActivity(b) ?? '') - Date.parse(lastActivity(a) ?? '')
  })
}

/** 还在这个房间手上的活 —— 在跑、排队、等验收、闲着。 */
const live = computed(() => sortRows(rows.value.filter((r) => !r.accepted_at && r.status !== 'closed')))
/** 交出去了的。 */
const delivered = computed(() => sortRows(rows.value.filter((r) => r.accepted_at)))
/** 关掉了，什么都没交付。 */
const abandoned = computed(() => sortRows(rows.value.filter((r) => !r.accepted_at && r.status === 'closed')))

const runningCount = computed(() => rows.value.filter((r) => taskRing(r).state === 'running').length)
const queuedCount = computed(() => rows.value.filter((r) => taskRing(r).state === 'queued').length)
</script>

<template>
  <section class="task-progress">
    <button
      type="button"
      class="task-progress__head"
      :aria-expanded="open"
      @click="open = !open"
    >
      <v-icon size="16">{{ open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
      <span class="task-progress__title t-body">Task Progress</span>
      <span class="task-progress__tally t-meta">
        <template v-if="rows.length">
          {{ rows.length }} 件<template v-if="runningCount">，{{ runningCount }} 件在跑</template>
          <template v-if="queuedCount">，{{ queuedCount }} 件排队</template>
        </template>
      </span>
    </button>

    <div v-if="open" class="task-progress__body">
      <div v-if="loading && !rows.length" class="d-flex justify-center py-4">
        <v-progress-circular indeterminate color="primary" size="20" />
      </div>

      <div v-else-if="errorMsg" class="px-3 py-2 t-body c-muted">{{ errorMsg }}</div>

      <div v-else-if="!rows.length" class="px-3 py-2">
        <div class="t-meta c-muted">还没有派出去的活</div>
      </div>

      <template v-else>
        <ul class="task-progress__list">
          <li v-for="row in live" :key="row.id">
            <button
              type="button"
              class="task-row"
              :class="{ 'task-row--here': row.id === topic?.id }"
              @click="emit('open-topic', row.id)"
            >
              <!-- 圆环：一个纯色环，颜色就是状态。不画百分比——一件活没有分母。 -->
              <span class="ring" :class="taskRing(row).cls" :title="taskRing(row).label" aria-hidden="true" />
              <span class="task-row__text">
                <span class="task-row__line1 t-body">
                  Task {{ numberOf.get(row.id) }}: {{ row.title }}
                </span>
                <span class="task-row__line2 t-meta">
                  <span class="task-row__state">{{ taskRing(row).label }}</span>
                  <span class="task-row__sep">·</span>
                  <span v-if="row.owner_handle">{{ row.owner_handle }}</span>
                  <span v-else class="c-faint">暂无负责人</span>
                  <span class="task-row__sep">·</span>
                  <span>{{ relTime(lastActivity(row)) }}</span>
                  <span v-if="row.id === topic?.id" class="task-row__here-tag">你在这</span>
                </span>
              </span>
            </button>
          </li>
        </ul>

        <!-- 已交付 / 已关闭未交付分开：一条交出去了的活是这个房间的产出，一条
             关掉却什么都没交付的是被放弃的。混在一起看不出交了多少。 -->
        <template v-for="group in [
          { key: 'delivered', label: '已交付', list: delivered },
          { key: 'abandoned', label: '已关闭 · 未交付', list: abandoned },
        ]" :key="group.key">
          <template v-if="group.list.length">
            <div class="task-progress__group t-meta">{{ group.label }}（{{ group.list.length }}）</div>
            <ul class="task-progress__list">
              <li v-for="row in group.list" :key="row.id">
                <button
                  type="button"
                  class="task-row task-row--done"
                  :class="{ 'task-row--here': row.id === topic?.id }"
                  @click="emit('open-topic', row.id)"
                >
                  <span class="ring" :class="taskRing(row).cls" :title="taskRing(row).label" aria-hidden="true" />
                  <span class="task-row__text">
                    <span class="task-row__line1 t-body">
                      Task {{ numberOf.get(row.id) }}: {{ row.title }}
                    </span>
                    <span class="task-row__line2 t-meta">
                      <span class="task-row__state">{{ taskRing(row).label }}</span>
                      <span class="task-row__sep">·</span>
                      <span v-if="row.owner_handle">{{ row.owner_handle }}</span>
                      <span v-else class="c-faint">暂无负责人</span>
                    </span>
                  </span>
                </button>
              </li>
            </ul>
          </template>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.task-progress {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
.task-progress__head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  cursor: pointer;
  text-align: left;
}
.task-progress__head:hover {
  background: var(--fill);
}
.task-progress__title {
  color: var(--ink);
  font-weight: 600;
}
.task-progress__tally {
  margin-left: auto;
  color: var(--faint);
}
.task-progress__body {
  max-height: 40vh;
  overflow-y: auto;
}
.task-progress__group {
  padding: 8px 12px 2px;
  color: var(--faint);
}
.task-progress__list {
  list-style: none;
  padding: 0 8px 6px;
  margin: 0;
}
.task-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
}
.task-row:hover {
  background: var(--fill);
}
/* 你正在看的那条。不是选中态（这一段不是导航），只是「这行就是你」。 */
.task-row--here {
  background: var(--fill);
}
/* 做完了的那两组压低一档，但不隐藏：它们是这个房间交出去了什么的记录。 */
.task-row--done {
  opacity: 0.72;
}
.task-row__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1 1 auto;
}
.task-row__line1 {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}
.task-row__line2 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
}
.task-row__sep {
  color: var(--faint);
}
.task-row__here-tag {
  margin-left: auto;
  flex: none;
  color: var(--faint);
}

/* 圆环：状态就是颜色。在跑的那一格转，因为「在跑」是唯一一个此刻还在变的状态。 */
.ring {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  margin-top: 4px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
.ring--running {
  border-color: var(--ok);
  border-right-color: transparent;
  animation: ring-spin 1.1s linear infinite;
}
.ring--queued {
  border-color: var(--warn, #d08700);
  border-style: dotted;
}
.ring--reviewing {
  border-color: var(--warn, #d08700);
}
.ring--delivered {
  border-color: var(--ok);
  background: var(--ok);
}
.ring--closed {
  border-color: var(--faint);
  background: var(--faint);
}
.ring--idle {
  border-color: var(--faint);
}
@keyframes ring-spin {
  to {
    transform: rotate(360deg);
  }
}
/* 有人把动效关了就别转 —— 状态靠颜色也说得清。 */
@media (prefers-reduced-motion: reduce) {
  .ring--running {
    animation: none;
    border-right-color: var(--ok);
  }
}
</style>
