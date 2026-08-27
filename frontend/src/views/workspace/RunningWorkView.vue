<script setup lang="ts">
// 在跑的活 —— 整个项目里此刻有什么在动，跨房间的一张表。
//
// 房间总览的 Task Progress 答的是「这个房间在干什么」，而一个项目有上百个房间：
// 想知道「现在整个项目有什么在跑」，就得一个个点进去，于是没人知道。四个槽位一
// 个房间的额度也只有在这里才看得出来撞没撞上——某个房间四条在跑三条排队，从那个
// 房间里面看是正常的，从这里看才是「它把队排到别处去了」。
//
// 只列在动的：在跑、排队、等验收。闲着的和做完的不在这里——它们在各自房间的总览
// 里，而这一页存在的理由就是「不必打开每个房间」。
import type { RoomTask, Topic } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { listProjectTasks } from '@/api'
import { relTime } from '@/lib/relTime'
import { ringRank, taskRing } from '@/lib/taskRing'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

const rows = ref<RoomTask[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)

async function load() {
  const pid = props.projectId
  loading.value = true
  errorMsg.value = null
  try {
    const payload = await listProjectTasks(pid)
    if (props.projectId === pid) rows.value = payload.data
  } catch {
    errorMsg.value = '加载失败'
  } finally {
    if (props.projectId === pid) loading.value = false
  }
}

onMounted(load)
watch(() => props.projectId, load)

/** 房间标题。侧栏那张表只列房间，所以它答得了这个问题。 */
const roomTitle = computed(() => {
  const byId = new Map((store.topics as Topic[]).map((t) => [t.id, t.title]))
  return (roomId: string) => byId.get(roomId) ?? '（房间已不在列表里）'
})

const MOVING = new Set(['running', 'queued', 'reviewing'])

const moving = computed(() =>
  rows.value
    .filter((r) => MOVING.has(taskRing(r).state))
    .sort((a, b) => {
      const rank = ringRank(taskRing(a).state) - ringRank(taskRing(b).state)
      if (rank !== 0) return rank
      // 同一格里按房间归堆，读起来才是「这个房间三条」而不是散着的九条。
      return a.room_id.localeCompare(b.room_id) || Date.parse(a.created_at) - Date.parse(b.created_at)
    })
)

/** 每个房间在跑几条 —— 撞额度的那些，一眼看得出来。 */
const runningPerRoom = computed(() => {
  const n = new Map<string, number>()
  for (const r of rows.value) {
    if (taskRing(r).state === 'running') n.set(r.room_id, (n.get(r.room_id) ?? 0) + 1)
  }
  return n
})

const tally = computed(() => {
  const by = { running: 0, queued: 0, reviewing: 0 }
  for (const r of rows.value) {
    const s = taskRing(r).state
    if (s in by) by[s as keyof typeof by] += 1
  }
  return by
})

function openTask(task: RoomTask) {
  void router.push({
    name: 'workspace-topic',
    params: { projectId: props.projectId, topicId: task.id },
  })
}
</script>

<template>
  <div class="running-work">
    <header class="running-work__head">
      <h1 class="t-title">在跑的活</h1>
      <p class="t-meta c-muted">
        <template v-if="rows.length">
          {{ tally.running }} 件在跑 · {{ tally.queued }} 件排队 · {{ tally.reviewing }} 件等验收
        </template>
        <template v-else-if="!loading">这个项目还没有派出去的活</template>
      </p>
    </header>

    <div v-if="loading && !rows.length" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>

    <div v-else-if="errorMsg" class="pa-6 t-body c-muted">
      {{ errorMsg }}
      <v-btn class="ms-2" size="small" variant="text" @click="load">重试</v-btn>
    </div>

    <div v-else-if="!moving.length" class="pa-6">
      <div class="t-body c-muted">现在没有在动的活</div>
      <div class="t-meta mt-1">闲着的和做完的不在这里 —— 它们在各自房间的总览里，这一页只答「现在有什么在动」。</div>
    </div>

    <ul v-else class="running-work__list">
      <li v-for="row in moving" :key="row.id">
        <button type="button" class="work-row" @click="openTask(row)">
          <span class="ring" :class="taskRing(row).cls" :title="taskRing(row).label" aria-hidden="true" />
          <span class="work-row__text">
            <span class="work-row__line1 t-body">{{ row.title }}</span>
            <span class="work-row__line2 t-meta">
              <span class="work-row__room">{{ roomTitle(row.room_id) }}</span>
              <!-- 这个房间四个槽位占满了：它后面那些是真的在等，不是没人理。 -->
              <span v-if="(runningPerRoom.get(row.room_id) ?? 0) >= 4" class="work-row__full">房间满员</span>
              <span class="work-row__sep">·</span>
              <span class="work-row__state">{{ taskRing(row).label }}</span>
              <span class="work-row__sep">·</span>
              <span v-if="row.owner_handle">{{ row.owner_handle }}</span>
              <span v-else class="c-faint">暂无负责人</span>
              <span class="work-row__sep">·</span>
              <span>{{ relTime(row.updated_at ?? row.created_at) }}</span>
            </span>
          </span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.running-work {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 12px 24px;
}
.running-work__head {
  padding: 0 10px 10px;
}
.running-work__list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.work-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
}
.work-row:hover {
  background: var(--fill);
}
.work-row__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1 1 auto;
}
.work-row__line1 {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}
.work-row__line2 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
}
.work-row__room {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 40%;
}
.work-row__full {
  flex: none;
  font-size: 12px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--fill);
}
.work-row__sep {
  color: var(--faint);
}

/* 圆环和房间总览里那个是同一套词、同一套颜色 —— 两处说的必须是同一件事。 */
.ring {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  margin-top: 5px;
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
@keyframes ring-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .ring--running {
    animation: none;
    border-right-color: var(--ok);
  }
}
</style>
