<script setup lang="ts">
// 看板 —— 房间总览最上面那一段：这个房间下面有哪些活，各自轮到谁动。
//
// 它取代了原来那个独立的「任务」tab。合并的理由不是省一个 tab：文档和这份清单
// 回答的是同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得
// 先想起来还有另一半。现在文档接在它下面，一屏就是全部。
//
// 和项目那块板是同一套列、同一套短语（`lib/board.ts`），只是范围缩到一个房间，而且
// 列是竖着堆的不是并排的——这里只有一条窄栏的宽度，并排三列一列放不下一张卡。列的
// 顺序和名字与那边一字不差：同一个词在两个地方指同一件事，人才不用在脑子里翻译。
//
// 每行一条活：色点 +「第 N 件：做什么」+ 小字写状态短语和负责人。屏幕上每一个状态
// 词都是后端算好的 `presentation.display_status`，这一段一个都不推。
import type { Block, BoardColumn, RoomTask, RoomTree, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { listRoomTasks, listRoomTrees } from '../../api'
import { BOARD_COLUMNS, columnDotStyle, columnLabel, compareTasks } from '../../lib/board'
import { relTime } from '../../lib/relTime'

const props = withDefaults(
  defineProps<{
    /** 当前打开的房间。 */
    topic: Topic | null
    /** 这一段在屏幕上。折叠起来的时候不去拉。 */
    active?: boolean
    /** 每有一轮动静就加一。 */
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0 }
)

const emit = defineEmits<{
  (e: 'open-card', taskId: string): void
  (e: 'count', n: number): void
}>()

type ThreadRow = RoomTask & { blocks?: Block[] }

const rows = ref<ThreadRow[]>([])
const trees = ref<RoomTree[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 默认展开：你打开一个房间的第一个问题就是「现在有什么在动」，折起来等于不答。
const open = ref(true)
// 已完成默认折起来。件数写在按钮上，所以折起来不等于藏起来。
const showDone = ref(false)

async function load() {
  const place = props.topic
  if (!place) {
    rows.value = []
    return
  }
  const roomId = place.id
  loading.value = true
  errorMsg.value = null
  try {
    // limit: 1 是必须的，不是优化。不传的话后端会把房间里**每一条**支线的完整
    // 历史都吐回来，而一个跑久了的房间有近两百条活。这里只要每条最新的那一块，
    // 用来说「最后活动」。
    const payload = await listRoomTasks(roomId, { limit: 1 })
    if (props.topic?.id === roomId) {
      rows.value = payload.data
      emit('count', payload.data.length)
    }
  } catch {
    errorMsg.value = '任务列表加载失败'
  } finally {
    if (props.topic?.id === roomId) loading.value = false
  }
  // 这一批封没封口，是另一个问题，也是另一条请求 —— 它失败了不该把整份清单变成
  // 一句「加载失败」，所以拿不到就当没有提示，清单照常。
  try {
    const batches = await listRoomTrees(roomId)
    if (props.topic?.id === roomId) trees.value = batches.data
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

/** 新动过的排前面，同一时刻的按 id 定序 —— 少了后面这一半，两条同秒的活谁在前面
 *  取决于响应里的数组顺序，于是这一段会在两次刷新之间自己跳。排序用的时间是「最后
 *  活动」，和行上显示的那个时间是同一个，不然看起来就是排错了。 */
function sortRows(list: ThreadRow[]): ThreadRow[] {
  return [...list].sort((a, b) =>
    compareTasks({ id: a.id, updated_at: lastActivity(a) }, { id: b.id, updated_at: lastActivity(b) })
  )
}

const byColumn = computed(() => {
  const buckets = new Map<BoardColumn, ThreadRow[]>()
  for (const row of rows.value) {
    const key = row.presentation.column
    const list = buckets.get(key)
    if (list) list.push(row)
    else buckets.set(key, [row])
  }
  return buckets
})

function inColumn(column: BoardColumn): ThreadRow[] {
  return sortRows(byColumn.value.get(column) ?? [])
}

/** 已完成收在最底下、折起来 —— 和项目那块板同一个处理。「已采纳」和「已收工」的
 *  区别没有丢：它们在同一列里是两个不同的短语，展开就看得见。 */
const doneRows = computed(() => inColumn('done'))
/** 这个房间此刻有几件在等人。它排在标题旁边，因为这是打开一个房间最该先看到的数。 */
const needsYouCount = computed(() => (byColumn.value.get('needs_you') ?? []).length)

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
      <span class="task-progress__title t-body">看板</span>
      <span class="task-progress__tally t-meta">
        <template v-if="rows.length">
          {{ rows.length }} 件<template v-if="needsYouCount">，{{ needsYouCount }} 件等你</template>
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
        <div class="t-meta c-muted">暂无派出去的活</div>
      </div>

      <template v-else>
        <!-- 三列竖着堆。空的那一列也留着列头和 0：整段消失会让这一段在两次刷新之
             间跳，而「等你」在哪个位置本身就是信息，不该取决于它此刻有没有东西。 -->
        <template v-for="col in BOARD_COLUMNS" :key="col.key">
          <div class="task-progress__group t-meta" :data-column="col.key">
            <span class="board-dot" :style="columnDotStyle(col.key)" aria-hidden="true" />
            <span>{{ col.label }}</span>
            <span class="task-progress__group-count">{{ inColumn(col.key).length }}</span>
          </div>
          <ul class="task-progress__list">
            <li v-for="row in inColumn(col.key)" :key="row.id">
              <button
                type="button"
                class="task-row"
                @click="emit('open-card', row.id)"
              >
                <span class="board-dot" :style="columnDotStyle(row.presentation.column)" aria-hidden="true" />
                <span class="task-row__text">
                  <span class="task-row__line1 t-body"> 第 {{ numberOf.get(row.id) }} 件：{{ row.title }}</span>
                  <span class="task-row__line2 t-meta">
                    <span class="task-row__state">{{ row.presentation.display_status }}</span>
                    <span class="task-row__sep">·</span>
                    <span v-if="row.owner_handle">{{ row.owner_handle }}</span>
                    <span v-else class="c-faint">暂无负责人</span>
                    <span class="task-row__sep">·</span>
                    <span>{{ relTime(lastActivity(row)) }}</span>
                  </span>
                </span>
              </button>
            </li>
          </ul>
        </template>

        <!-- 已完成收在最底下、折起来。「已采纳」和「已收工」的区别没有丢：它们在
             这一列里是两个不同的短语，展开就看得见。 -->
        <template v-if="doneRows.length">
          <button
            type="button"
            class="task-progress__group task-progress__group--fold t-meta"
            :aria-expanded="showDone"
            @click="showDone = !showDone"
          >
            <v-icon size="14">{{ showDone ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
            <span>{{ columnLabel('done') }}</span>
            <span class="task-progress__group-count">{{ doneRows.length }}</span>
          </button>
          <ul v-if="showDone" class="task-progress__list">
            <li v-for="row in doneRows" :key="row.id">
              <button
                type="button"
                class="task-row task-row--done"
                @click="emit('open-card', row.id)"
              >
                <span class="board-dot" :style="columnDotStyle(row.presentation.column)" aria-hidden="true" />
                <span class="task-row__text">
                  <span class="task-row__line1 t-body"> 第 {{ numberOf.get(row.id) }} 件：{{ row.title }}</span>
                  <span class="task-row__line2 t-meta">
                    <span class="task-row__state">{{ row.presentation.display_status }}</span>
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
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px 2px;
  color: var(--faint);
}
.task-progress__group--fold {
  padding-bottom: 6px;
  text-align: left;
  cursor: pointer;
}
.task-progress__group--fold:hover {
  color: var(--muted);
}
/* 计数右对齐。一列有几件是这一段最有用的信息之一，不是装饰。 */
.task-progress__group-count {
  margin-left: auto;
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

/* 色点：颜色和形状都由 `lib/board.ts` 一处给出（内联样式），这里只管尺寸 ——
   scoped 样式进不了别的组件，颜色写在这儿就意味着看板和侧栏各有一份。 */
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
/* 列头上那个点不跟着行走基线。 */
.task-progress__group .board-dot {
  margin-top: 0;
}

/* 批次的圆环。批次不是看板的一列——它答的是「我写的东西进哪个 PR」，另一个问题，
   所以是另一套点。还在收活的是空心的（东西还能往里放），封了口的是黄的（CI 在看
   它，别再动），合了的是实心的（已经落地）。 */
.ring {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  margin-top: 4px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
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
</style>
