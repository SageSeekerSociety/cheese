<script setup lang="ts">
// 看板 —— 整个项目的活摆在一块板上，按「**该谁动**」分列。
//
// 它以前是一张表，答的是「有哪些活、它们各是什么状态」。板答的是另一个问题：现在
// 轮到谁。一个人早上打开界面真正想知道的是后者——哪几件在等我、哪几件平台自己在
// 推、哪几件还在干。所以列不是状态的分组，是「下一步在谁手上」的分组：同一个客观
// 事实（快检红了）在平台自己修的时候落「交付中」，在等人拍板的时候落「等你」。
//
// 房间总览那一段答的是「这个房间在干什么」，而一个项目有上百个房间：不打开每个房
// 间就不知道现在什么在跑。四个槽位一个房间的额度也只有在这里才看得出来撞没撞上
// ——某个房间四条在跑三条排队，从那个房间里面看是正常的，从这里看才是「它把队排
// 到别处去了」。这两条理由是这一页存在的全部原因，改成板不能把它们弄丢。
//
// 屏幕上每一个状态词都是后端算好的 `presentation.display_status`，这里一个都不推。
import type { BoardColumn, RoomTask, Topic } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { listProjectTasks } from '@/api'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { BOARD_COLUMNS, columnDotStyle, columnLabel, compareTasks } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

const rows = ref<RoomTask[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 已完成折起来。板面留给还需要人看的东西，但要说得出有多少件——悄悄不显示会让人
// 以为这个项目从来没交付过什么。
const showDone = ref(false)

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

/** 房间标题。跨房间的板上，「这条活是哪个房间的」才是有用的坐标。
 *
 *  截图里 AO 的每张卡显示的是分支名，我们这里显示不了：一条活**没有自己的分支**，
 *  多条活共用一棵树、一棵树开一个 PR。照抄那一行只会写出一个假的事实。 */
const roomTitle = computed(() => {
  const byId = new Map((store.topics as Topic[]).map((t) => [t.id, t.title]))
  return (roomId: string) => byId.get(roomId) ?? '（房间已不在列表里）'
})

const byColumn = computed(() => {
  const buckets = new Map<BoardColumn, RoomTask[]>()
  for (const row of rows.value) {
    const key = row.presentation.column
    const list = buckets.get(key)
    if (list) list.push(row)
    else buckets.set(key, [row])
  }
  for (const list of buckets.values()) list.sort(compareTasks)
  return buckets
})

function inColumn(column: BoardColumn): RoomTask[] {
  return byColumn.value.get(column) ?? []
}

/** 每个房间在跑几条 —— 撞额度的那些，一眼看得出来。
 *
 *  「运行中」是 `building` 那一列的短语之一，由后端定；这里只是数它出现了几次，
 *  不是再推一次状态。 */
const runningPerRoom = computed(() => {
  const n = new Map<string, number>()
  for (const r of rows.value) {
    if (r.presentation.display_status === '运行中') n.set(r.room_id, (n.get(r.room_id) ?? 0) + 1)
  }
  return n
})

const doneRows = computed(() => inColumn('done'))

/** 顶上那行统计。以前是「N 件在跑 · N 件排队 · N 件等验收」，现在用板自己的词——
 *  三列的计数各自也在列头上，这一行是把它们和折起来的「已完成」并成一句。 */
const tally = computed(() =>
  [
    ...BOARD_COLUMNS.map((c) => ({ label: c.label, n: inColumn(c.key).length })),
    { label: '已完成', n: doneRows.value.length },
  ].filter((t) => t.n > 0)
)

// 打开一张卡 = 打开**它所在的房间**，然后在总览那一格钻进这张卡。一件活不是
// 地点：做它的分身住在房间的会话里，没有自己的地址。
function openTask(task: RoomTask) {
  void router.push({
    name: 'workspace-topic',
    params: { projectId: props.projectId, topicId: task.room_id },
    query: { tab: 'overview', card: task.id },
  })
}
</script>

<template>
  <div class="board">
    <header class="board__head">
      <h1 class="t-title">看板</h1>
      <p class="t-meta c-muted">
        <template v-if="tally.length">
          <template v-for="(t, i) in tally" :key="t.label">
            <span v-if="i" class="board__sep">·</span>
            {{ t.label }} {{ t.n }}
          </template>
        </template>
        <template v-else-if="!loading">暂无派出去的活</template>
      </p>
    </header>

    <LoadingSkeleton v-if="loading && !rows.length" variant="list" class="py-4" />

    <div v-else-if="errorMsg" class="pa-6 t-body c-muted">
      {{ errorMsg }}
      <v-btn class="ms-2" size="small" variant="text" @click="load">重试</v-btn>
    </div>

    <template v-else>
      <!-- 列永远都在，空了也留着列头和 0。整列消失会让板在两次刷新之间跳，而位置
           本身就是信息：「等你」那一列在哪儿，不该取决于它此刻有没有东西。 -->
      <div class="board__cols">
        <section v-for="col in BOARD_COLUMNS" :key="col.key" class="board-col" :data-column="col.key">
          <header class="board-col__head">
            <span class="board-dot" :class="col.cls" :style="columnDotStyle(col.key)" aria-hidden="true" />
            <span class="board-col__name t-body">{{ col.label }}</span>
            <span class="board-col__count t-meta">{{ inColumn(col.key).length }}</span>
          </header>
          <ul class="board-col__list">
            <li v-for="row in inColumn(col.key)" :key="row.id">
              <button type="button" class="board-card" @click="openTask(row)">
                <span class="board-card__title t-body">{{ row.title }}</span>
                <span class="board-card__owner t-meta">
                  <span class="board-card__room">{{ roomTitle(row.room_id) }}</span>
                  <span class="board-card__sep">·</span>
                  <span v-if="row.owner_handle">{{ row.owner_handle }}</span>
                  <span v-else class="c-faint">暂无负责人</span>
                  <!-- 这个房间四个槽位占满了：它后面那些是真的在等，不是没人理。 -->
                  <span v-if="(runningPerRoom.get(row.room_id) ?? 0) >= 4" class="board-card__full">房间满员</span>
                </span>
                <span class="board-card__rule" aria-hidden="true" />
                <span class="board-card__status t-meta">
                  <span class="board-dot" :style="columnDotStyle(row.presentation.column)" aria-hidden="true" />
                  <span class="board-card__phrase">{{ row.presentation.display_status }}</span>
                  <span class="board-card__when">{{ relTime(row.updated_at ?? row.created_at) }}</span>
                </span>
                <!-- 一条活骑一张卡、一棵树开一个 PR，是一对一 —— 所以这里永远只有
                     一个号，不为多个 PR 留结构。 -->
                <span v-if="row.card?.pr_number" class="board-card__pr t-meta">PR #{{ row.card.pr_number }}</span>
              </button>
            </li>
          </ul>
        </section>
      </div>

      <!-- 已完成收在底部。不是隐藏：件数写在按钮上，谁想看点开就是。 -->
      <div v-if="doneRows.length" class="board__done">
        <button type="button" class="board__done-head" :aria-expanded="showDone" @click="showDone = !showDone">
          <v-icon size="16">{{ showDone ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          <span class="t-body">{{ columnLabel('done') }}</span>
          <span class="t-meta board-col__count">{{ doneRows.length }}</span>
        </button>
        <ul v-if="showDone" class="board__done-list">
          <li v-for="row in doneRows" :key="row.id">
            <button type="button" class="done-row" @click="openTask(row)">
              <span class="board-dot" :style="columnDotStyle(row.presentation.column)" aria-hidden="true" />
              <span class="done-row__title t-body">{{ row.title }}</span>
              <span class="done-row__room t-meta">{{ roomTitle(row.room_id) }}</span>
              <span class="done-row__phrase t-meta">{{ row.presentation.display_status }}</span>
            </button>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.board {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  padding: 16px 12px 12px;
}
.board__head {
  flex: 0 0 auto;
  padding: 0 10px 10px;
}
.board__sep {
  color: var(--faint);
  margin: 0 4px;
}

/* 并排的纵向卡片流。min() 是让轨道不比容纳它的那一列更宽的那一半：光写
   minmax(260px, …) 的话 260px 是个下限，网格在一个更窄的容器里也照守，于是板横着
   溢出而不是重排 —— main 上 #658 就是修的这个。 */
.board__cols {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
  gap: 10px;
}
.board-col {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.board-col__head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}
.board-col__name {
  color: var(--ink);
  font-weight: 600;
}
/* 计数右对齐。它不是装饰：一列有几件是这块板最有用的信息之一。 */
.board-col__count {
  margin-left: auto;
  color: var(--muted);
}
.board-col__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 8px;
}

.board-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--canvas);
  text-align: left;
  cursor: pointer;
}
.board-card:hover {
  border-color: var(--line-2);
}
/* 标题最多两行，超出截断 —— 一张卡不该因为标题长就把整列推下去。 */
.board-card__title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--ink);
}
.board-card__owner {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
}
.board-card__room {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board-card__sep {
  color: var(--faint);
}
.board-card__full {
  flex: none;
  margin-left: auto;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--fill);
}
.board-card__rule {
  height: 1px;
  margin: 4px 0 2px;
  background: var(--line);
}
.board-card__status {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
}
.board-card__phrase {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board-card__when {
  margin-left: auto;
  flex: none;
  color: var(--faint);
}
.board-card__pr {
  color: var(--muted);
}

.board__done {
  flex: 0 0 auto;
  margin-top: 10px;
  border-top: 1px solid var(--line);
}
.board__done-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  text-align: left;
  cursor: pointer;
  color: var(--ink);
}
.board__done-head:hover {
  background: var(--fill);
}
.board__done-list {
  list-style: none;
  margin: 0;
  padding: 0 8px 8px;
  max-height: 28vh;
  overflow-y: auto;
}
.done-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
}
.done-row:hover {
  background: var(--fill);
}
.done-row__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.done-row__room,
.done-row__phrase {
  flex: none;
  color: var(--muted);
}
.done-row__phrase {
  margin-left: auto;
}

/* 色点。形状和颜色都由 `lib/board.ts` 一处给出（内联样式），这里只管尺寸 ——
   scoped 样式进不了别的组件，颜色写在这儿就意味着侧栏和房间总览各有一份。 */
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
</style>
