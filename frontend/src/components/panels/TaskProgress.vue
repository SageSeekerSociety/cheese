<script setup lang="ts">
// 派出去的活 —— 房间总览最上面那一段：这个房间下面有哪些活，各自处在哪一格。
//
// 它取代了原来那个独立的「任务」tab。合并的理由不是省一个 tab：文档和这份清单
// 回答的是同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得
// 先想起来还有另一半。现在文档接在它下面，一屏就是全部。
//
// 每行一条活：状态圆环 +「第 N 件：做什么」+ 小字写 subagent 和负责人。圆环显示
// 状态不显示百分比（`lib/taskRing.ts` 说明了为什么没有百分比可显示）。
//
// 「已交付 / 已关闭未交付」分两段列，因为它们不是同一件事：一条已交付的活是这个
// 房间的产出，一条关掉却什么都没交付的活是被放弃的——混在一起看不出这个房间到底
// 交出去了多少。
import type { Block, RoomTask, RoomTree, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { listRoomTasks, listRoomTrees } from '../../api'
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
const trees = ref<RoomTree[]>([])
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
  // 这一批封没封口，是另一个问题，也是另一条请求 —— 它失败了不该把整份清单变成
  // 一句「加载失败」，所以拿不到就当没有提示，清单照常。
  try {
    const batches = await listRoomTrees(roomId)
    if (props.topic && roomIdOf(props.topic) === roomId) trees.value = batches.data
  } catch {
    trees.value = []
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

/** 「第 N 件」的 N —— 按这个房间派活的先后，和界面上怎么排序无关。
 *
 *  编号必须稳定：它是人在对话里指代一条活的方式（「第 3 件卡住了」），跟着排序
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

// ---- 封口期 ----
// 一棵树 = 一个分支 = 一个 PR = 一批活。递卡的那一刻这一批封口，房间开下一棵接着
// 干 —— 这正是房间不再被一个在飞的 PR 冻住的原因。但代价是「我现在写的东西进的
// 是哪一批」变成了一个真问题，而封了口的房间和没封口的在屏幕上长得一模一样：
// 「我改了半天，改动怎么不在 PR 上」就是这么来的。
const sealed = computed(() => trees.value.filter((t) => t.status === 'sealed'))
/** 最新那一棵。房间现在写的东西进的就是它。 */
const current = computed<RoomTree | null>(() => trees.value[0] ?? null)
// 只在真的有一批在飞的时候说话。房间只有一棵开着的树时，「进这一批」是废话。
const sealNotice = computed(() => {
  if (!sealed.value.length) return null
  const n = sealed.value.length
  const riding = sealed.value.map((t) => t.card?.pr_number).filter((x): x is number => !!x)
  return {
    count: n,
    prs: riding,
    // 最新那一棵还开着 = 现在写的进的是下一批；最新那一棵就是封了口的那棵 =
    // 这个房间此刻整个在封口期，写什么都得等它。
    nextIsOpen: current.value?.status === 'open',
  }
})

// ---- 批次清单 ----
// 上面那行提示只说「现在写的进哪一批」；它答不了「我那条活最后从哪个 PR 出去」。
// 一个房间干久了会有好几批、好几个 PR，而在这份清单之前 PR 号只在当前那张验收卡
// 上出现过一次 —— 于是多个 PR 一并存就分不清哪个是哪个。这一段按批次列出来：
// 每批什么状态、骑在哪个 PR 上、里面有几件活。
const BATCH_STATE_LABEL: Record<RoomTree['status'], string> = {
  open: '在收活',
  sealed: '已封口 · CI 在跑',
  merged: '已合并',
}

/** 批次编号 —— 和活的编号同一个道理：按开出来的先后，老的是 1，不跟着排序变。 */
const batchNumberOf = computed(() => {
  const byBirth = [...trees.value].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  return new Map(byBirth.map((t, i) => [t.id, i + 1]))
})

/** 一批最后一次动的时间：合了看合的时候，封了看封的时候，还开着就是它开出来的时候。 */
function batchTime(t: RoomTree): string | null {
  if (t.status === 'merged') return t.merged_at ?? t.sealed_at ?? t.created_at
  if (t.status === 'sealed') return t.sealed_at ?? t.created_at
  return t.created_at
}

const batches = computed(() =>
  trees.value.map((t) => ({
    id: t.id,
    n: batchNumberOf.value.get(t.id) ?? 0,
    status: t.status,
    stateLabel: BATCH_STATE_LABEL[t.status] ?? t.status,
    prNumber: t.card?.pr_number ?? null,
    prUrl: t.card?.pr_url ?? null,
    // 这一批里有几件派出去的活。活自己带着它干在哪棵树上，所以不用再问一次后端。
    taskCount: rows.value.filter((r) => r.tree_id === t.id).length,
    // 快检谁也不拦，但红了得让将要验收的人看见。
    checkFailed: t.last_check_ok === false,
    // 最新那一棵还开着 = 现在写的东西进的就是它。
    isCurrent: t.id === current.value?.id && t.status === 'open',
    at: batchTime(t),
  }))
)

// 只有一批、而且它还没开出 PR 的时候，「哪一批」根本不是个问题，这一段是纯噪声。
const showBatches = computed(() => batches.value.length > 1 || batches.value.some((b) => b.prNumber))

// 老批次折起来，但**说出折了几批** —— 悄悄截断会让人以为这就是全部。
const BATCH_HEAD = 5
const allBatches = ref(false)
const shownBatches = computed(() => (allBatches.value ? batches.value : batches.value.slice(0, BATCH_HEAD)))
const hiddenBatches = computed(() => batches.value.length - shownBatches.value.length)
</script>

<template>
  <section class="task-progress">
    <button type="button" class="task-progress__head" :aria-expanded="open" @click="open = !open">
      <v-icon size="16">{{ open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
      <span class="task-progress__title t-body">派出去的活</span>
      <span class="task-progress__tally t-meta">
        <template v-if="rows.length">
          {{ rows.length }} 件<template v-if="runningCount">，{{ runningCount }} 件在跑</template>
          <template v-if="queuedCount">，{{ queuedCount }} 件排队</template>
        </template>
      </span>
    </button>

    <!-- 封口期提示。在清单外面而不是里面：它说的是「你现在写的东西去哪儿」，
         对这个房间的每一条活都成立，而且折起来也该看得见。 -->
    <div v-if="sealNotice" class="seal-notice t-meta" data-testid="seal-notice">
      <v-icon size="14">mdi-lock-outline</v-icon>
      <span>
        <template v-if="sealNotice.nextIsOpen">
          有 {{ sealNotice.count }} 批已封口在跑 CI<template v-if="sealNotice.prs.length"
            >（{{ sealNotice.prs.map((n) => `#${n}`).join('、') }}）</template
          >；现在写的进下一批。
        </template>
        <template v-else>
          这一批已封口<template v-if="sealNotice.prs.length"
            >（{{ sealNotice.prs.map((n) => `#${n}`).join('、') }}）</template
          >，内容就是 CI 正在检查的东西 —— 现在别再往里写。
        </template>
      </span>
    </div>

    <div v-if="open" class="task-progress__body">
      <!-- 批次。在活的清单之上，因为它是那份清单的坐标系：一条活最后从哪个 PR
           出去，取决于它在哪一批。房间自己写的东西也在批次里，所以一批可以一件
           派出去的活都没有 —— 那时候不写件数，别编。 -->
      <template v-if="showBatches">
        <div class="task-progress__group t-meta">批次 · 一批活出一个 PR（{{ batches.length }}）</div>
        <ul class="task-progress__list" data-testid="batch-list">
          <li v-for="b in shownBatches" :key="b.id">
            <div class="batch-row" :data-testid="`batch-${b.id}`">
              <span class="ring" :class="`ring--batch-${b.status}`" :title="b.stateLabel" aria-hidden="true" />
              <span class="task-row__text">
                <span class="task-row__line1 t-body">
                  第 {{ b.n }} 批 · {{ b.stateLabel }}
                  <a
                    v-if="b.prNumber && b.prUrl"
                    class="batch-row__pr"
                    :href="b.prUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                    >PR #{{ b.prNumber }}</a
                  >
                  <span v-else-if="b.prNumber" class="batch-row__pr">PR #{{ b.prNumber }}</span>
                  <span v-else class="batch-row__pr c-faint">还没开 PR</span>
                </span>
                <span class="task-row__line2 t-meta">
                  <span v-if="b.taskCount">{{ b.taskCount }} 件活</span>
                  <span v-if="b.taskCount" class="task-row__sep">·</span>
                  <span>{{ relTime(b.at) }}</span>
                  <span v-if="b.checkFailed" class="batch-row__check">快检没过</span>
                  <span v-if="b.isCurrent" class="task-row__here-tag">现在写的进这一批</span>
                </span>
              </span>
            </div>
          </li>
        </ul>
        <!-- 折起来的批次要说出有几批，不然看起来就是全部。 -->
        <button v-if="hiddenBatches" type="button" class="batch-more t-meta" @click="allBatches = true">
          还有 {{ hiddenBatches }} 批更早的
        </button>
      </template>

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
                <span class="task-row__line1 t-body"> 第 {{ numberOf.get(row.id) }} 件：{{ row.title }}</span>
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
        <template
          v-for="group in [
            { key: 'delivered', label: '已交付', list: delivered },
            { key: 'abandoned', label: '已关闭 · 未交付', list: abandoned },
          ]"
          :key="group.key"
        >
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
                    <span class="task-row__line1 t-body"> 第 {{ numberOf.get(row.id) }} 件：{{ row.title }}</span>
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
.seal-notice {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 12px 8px;
  color: var(--muted);
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

/* 批次那几行。不是按钮：一批活没有「打开」这回事，能点的只有它的 PR。 */
.batch-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
}
.batch-row__pr {
  color: var(--muted);
}
a.batch-row__pr:hover {
  color: var(--ink);
  text-decoration: underline;
}
/* 快检红了不拦任何人（PR 上真的 CI 才拦），但要验收的人得看见。用 -ink 那一档：
   `--danger` 本身是给点和边框的，当正文在浅色下对比度不够。 */
.batch-row__check {
  color: var(--danger-ink);
}
.batch-more {
  display: block;
  width: 100%;
  padding: 0 12px 8px;
  text-align: left;
  color: var(--muted);
  cursor: pointer;
}
.batch-more:hover {
  color: var(--ink);
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
  border-color: var(--warn);
  border-style: dotted;
}
.ring--reviewing {
  border-color: var(--warn);
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
/* 批次的点：还在收活的是空心的（东西还能往里放），封了口的是黄的（CI 在看它，
   别再动），合了的是实心的（已经落地）。 */
.ring--batch-open {
  border-color: var(--ok);
}
.ring--batch-sealed {
  border-color: var(--warn);
}
.ring--batch-merged {
  border-color: var(--ok);
  background: var(--ok);
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
