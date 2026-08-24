<script setup lang="ts">
// 任务 tab: 这个房间派出去的活，一条一行。
//
// 在它之前，一条支线在界面上**根本不可见**：`GET /topics/{room}/tasks` 全仓库只有
// ChatPanel 一个调用点，而且只是为了在时间线上画那条「已派出」标记——那条标记只在
// 它派出去的那一刻**那个时间点**出现，往下滚就没了。于是房间里没有任何地方回答得了
// 「现在有哪些活在跑、谁在做、做到哪了」，房间照着自己那份清单又做一遍。
//
// 为什么是这里而不是侧栏：一件活不占侧栏一行，是把活从话题里拆出来省下的那笔成本
// （169 个话题的侧栏已经够长了）。它属于**它所在的房间里面**，和文档/现场/改动
// 一样是这个房间的一个面。
import type { Block, RoomTask, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { listRoomTasks } from '../../api'
import { roomIdOf } from '../../lib/place'
import { relTime } from '../../lib/relTime'
import { topicStateBadge } from '../../lib/topicState'

const props = withDefaults(
  defineProps<{
    /** 当前打开的地点。是支线时列的仍然是**它所在房间**的活（包括它自己）。 */
    topic: Topic | null
    /** 这一格在屏幕上。和别的 tab 一样，上升沿才去拉。 */
    active?: boolean
    /** 每有一轮动静就加一，用来把「最后活动」刷新。 */
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const emit = defineEmits<{ (e: 'open-topic', topicId: string): void }>()

type ThreadRow = RoomTask & { blocks?: Block[] }

const rows = ref<ThreadRow[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)

async function load() {
  const place = props.topic
  if (!place) {
    rows.value = []
    return
  }
  // 活挂在房间上，所以问的永远是房间——在一条支线里打开这一格，看到的是它的同伴。
  const roomId = roomIdOf(place)
  loading.value = true
  errorMsg.value = null
  try {
    // limit: 1 是必须的，不是优化。不传的话后端会把房间里**每一条**支线的完整
    // 历史都吐回来（它的 docstring 说明了为什么故意没有默认上限），而一个跑久了
    // 的房间有近两百条活。这里只需要每条最新的那一块，用来说「最后活动」。
    const payload = await listRoomTasks(roomId, { limit: 1 })
    if (props.topic && roomIdOf(props.topic) === roomId) rows.value = payload.data
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

// 在跑的排前面，然后按最后活动从新到旧 —— 你来这一格是想知道「现在有什么在动」。
const ordered = computed(() =>
  [...rows.value].sort((a, b) => {
    const openness = Number(b.status === 'open') - Number(a.status === 'open')
    if (openness !== 0) return openness
    return Date.parse(lastActivity(b) ?? '') - Date.parse(lastActivity(a) ?? '')
  })
)

const openCount = computed(() => rows.value.filter((r) => r.status === 'open').length)
</script>

<template>
  <div class="panel-tasks">
    <div v-if="loading && !rows.length" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>

    <div v-else-if="errorMsg" class="pa-4 t-body c-muted">{{ errorMsg }}</div>

    <div v-else-if="!rows.length" class="pa-4">
      <div class="t-body c-muted">暂无派出的活</div>
      <div class="t-meta mt-1">房间里的活是从这里的对话派出去的，派出后会在这一格列出来</div>
    </div>

    <template v-else>
      <div class="panel-tasks__head t-meta">
        共 {{ rows.length }} 件<template v-if="openCount">，{{ openCount }} 件进行中</template>
      </div>
      <ul class="panel-tasks__list">
        <li v-for="row in ordered" :key="row.id">
          <button
            type="button"
            class="task-row"
            :class="{ 'task-row--here': row.id === topic?.id }"
            @click="emit('open-topic', row.id)"
          >
            <span class="task-row__line1">
              <span class="task-row__title t-body">{{ row.title }}</span>
              <span class="pr-state" :class="topicStateBadge(row.status).cls">
                {{ topicStateBadge(row.status).label }}
              </span>
            </span>
            <span class="task-row__line2 t-meta">
              <!-- 唯一的主。房间用名册回答「谁在这」，一件活用一个 handle。 -->
              <span v-if="row.owner_handle" class="task-row__owner">{{ row.owner_handle }}</span>
              <span v-else class="task-row__owner c-faint">暂无负责人</span>
              <span class="task-row__sep">·</span>
              <span>{{ relTime(lastActivity(row)) }}</span>
              <span v-if="row.id === topic?.id" class="task-row__here-tag">你在这</span>
            </span>
          </button>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.panel-tasks {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.panel-tasks__head {
  padding: 10px 12px 6px;
  color: var(--faint);
}
.panel-tasks__list {
  list-style: none;
  padding: 0 8px 12px;
  margin: 0;
}
.task-row {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
}
.task-row:hover {
  background: var(--fill);
}
/* 你正在看的那条。不是选中态（这一格不是导航），只是「这行就是你」。 */
.task-row--here {
  background: var(--fill);
}
.task-row__line1 {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.task-row__title {
  flex: 1 1 auto;
  min-width: 0;
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
.task-row__owner {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-row__sep {
  color: var(--faint);
}
.task-row__here-tag {
  margin-left: auto;
  flex: none;
  color: var(--faint);
}
/* 状态标和话题头部读的是同一张表 (lib/topicState.ts)，所以「已完成」在两处
   永远是同一个词、同一个颜色。 */
.pr-state {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}
.pr-state--open {
  color: var(--surface);
  background: var(--ok);
}
.pr-state--merged {
  color: var(--muted);
  background: var(--fill);
}
.pr-state--draft {
  color: var(--faint);
  background: var(--fill);
}
</style>
