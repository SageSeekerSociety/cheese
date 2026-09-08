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
import type { BoardColumn, ProjectMemberRow, RoomTask, Topic } from '@/cx_types'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { avatarColor, avatarInitial } from '@/utils/avatar'
import { getAvatarUrl } from '@/utils/materials'

import { listProjectTasks } from '@/api'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { BOARD_COLUMNS, columnDotStyle, columnLabel, compareTasks } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()

const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

const rows = ref<RoomTask[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 已完成折起来。板面留给还需要人看的东西，但要说得出有多少件——悄悄不显示会让人
// 以为这个项目从来没交付过什么。
const showDone = ref(false)

/** 板隔多久自己重拉一次。
 *
 *  它是进项目的第一屏，而上面每一个短语都是后端算的：后端那边一直在动，板不动就
 *  是一张会骗人的快照。15 秒是「一条活从排队变成运行中」这个尺度上人还愿意等的
 *  长度；外壳那条 30 秒的 tick 刷的是未读角标，节奏本来就该比这块板慢。 */
const REFRESH_MS = 15_000

/** 重拉。
 *
 *  `silent` 的一次不碰 `loading`、不清 `rows`、失败也不写 `errorMsg` —— 正在看的
 *  那几列因此不会闪回骨架，也不会因为一次网络抖动整块变成「加载失败」。同
 *  `TopicAcceptCard.vue` 的 `loadAcceptCard(silent)`，那里的注释解释了为什么这两
 *  种加载必须分开。 */
let inFlight = false
async function load(silent = false) {
  const pid = props.projectId
  // 上一次还没回来就跳过：慢响应叠在一起会让 rows 按乱序落地，板于是自己跳。
  if (silent && inFlight) return
  inFlight = true
  if (!silent) {
    loading.value = true
    errorMsg.value = null
  }
  try {
    const payload = await listProjectTasks(pid)
    if (props.projectId === pid) {
      rows.value = payload.data
      // 上一次前台加载失败过、这一次悄悄成功了：把错误收掉，人不用自己点重试。
      errorMsg.value = null
    }
  } catch {
    if (!silent && props.projectId === pid) errorMsg.value = '加载失败'
  } finally {
    inFlight = false
    if (!silent && props.projectId === pid) loading.value = false
  }
}

let timer: number | undefined
function tick() {
  // 后台标签页没必要空转。切回来的那一下补一次，看到的是此刻的板而不是离开时的。
  if (document.hidden) return
  void load(true)
}
function onVisibility() {
  if (!document.hidden) void load(true)
}

onMounted(() => {
  void load()
  timer = window.setInterval(tick, REFRESH_MS)
  document.addEventListener('visibilitychange', onVisibility)
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisibility)
})
// 换项目是一次前台加载：换过去的那一刻板上还挂着上一个项目的活，那必须重画。
watch(
  () => props.projectId,
  () => void load()
)

/** 房间标题。跨房间的板上，「这条活是哪个房间的」才是有用的坐标。
 *
 *  截图里 AO 的每张卡显示的是分支名，我们这里显示不了：一条活**没有自己的分支**，
 *  多条活共用一棵树、一棵树开一个 PR。照抄那一行只会写出一个假的事实。 */
const roomTitle = computed(() => {
  const byId = new Map((store.topics as Topic[]).map((t) => [t.id, t.title]))
  return (roomId: string) => byId.get(roomId) ?? '（房间已不在列表里）'
})

// 「谁在做」是一个人，不是一个 handle。名册里有昵称和他自己挑的头像，卡上就该是
// 那两样——`n1ctheboy` 这种串认得出来的只有他本人。
const memberByHandle = computed(() => new Map((store.members as ProjectMemberRow[]).map((m) => [m.user_handle, m])))

/** 名册上的昵称；名册里没有这个 handle 就把 handle 原样显示出来（同
 *  `ChatPanel.vue` 的 `displayName`）—— 退回空白等于把「这条活有主」也一起抹掉。 */
function ownerName(handle?: string | null): string {
  if (!handle) return ''
  return memberByHandle.value.get(handle)?.name || handle
}

// 真头像加载失败过的 handle —— 退回彩色首字母，不留破图。
const avatarBroken = ref<Set<string>>(new Set())
function avatarSrc(handle?: string | null): string | null {
  if (!handle || avatarBroken.value.has(handle)) return null
  const id = memberByHandle.value.get(handle)?.avatar_id
  // 名册上没这个人、或这行没有头像时返回 null：宁可留一个按 handle 哈希、认得出
  // 是谁的色块，也不要 getAvatarUrl(undefined) 给所有没挑过头像的人配同一张脸。
  return id == null ? null : getAvatarUrl(id)
}
function onAvatarError(handle?: string | null): void {
  if (!handle || avatarBroken.value.has(handle)) return
  avatarBroken.value = new Set(avatarBroken.value).add(handle)
}

/** 这会儿真的在跑的那些。
 *
 *  「运行中」是后端 `display_status` 的原词，这里只是认它，没有在前端另推一次状
 *  态：色点是**列级**的，所以「施工中」那一列里在跑的和排队的原来长得一模一样，
 *  而这两件事对看的人不是一回事。 */
function isRunning(row: RoomTask): boolean {
  return row.presentation.display_status === '运行中'
}

/** 「只看我的」。
 *
 *  开关住在地址栏（`?mine=1`）而不是组件状态里：一刷新就丢会让人反复点，而写进地
 *  址还顺带让「我手上这些」变成一条能发出去的链接。 */
const mineHandle = computed(() => myHandle())
const mine = computed(() => route.query.mine === '1')
function toggleMine() {
  const query = { ...route.query }
  if (mine.value) delete query.mine
  else query.mine = '1'
  void router.replace({ query })
}

const visibleRows = computed(() =>
  mine.value && mineHandle.value ? rows.value.filter((r) => r.owner_handle === mineHandle.value) : rows.value
)

function bucket(list: RoomTask[]): Map<BoardColumn, RoomTask[]> {
  const buckets = new Map<BoardColumn, RoomTask[]>()
  for (const row of list) {
    const key = row.presentation.column
    const existing = buckets.get(key)
    if (existing) existing.push(row)
    else buckets.set(key, [row])
  }
  for (const rowsInColumn of buckets.values()) rowsInColumn.sort(compareTasks)
  return buckets
}

const byColumn = computed(() => bucket(visibleRows.value))
const totalByColumn = computed(() => bucket(rows.value))

function inColumn(column: BoardColumn): RoomTask[] {
  return byColumn.value.get(column) ?? []
}

/** 列头上那个数。开关一开就写成「3 / 12」——只写 3 的话，人会以为活丢了。 */
function countLabel(column: BoardColumn): string {
  const shown = inColumn(column).length
  if (!mine.value) return String(shown)
  return `${shown} / ${totalByColumn.value.get(column)?.length ?? 0}`
}

/** 一列空着的时候，那一列自己说它空。
 *
 *  这句话是第一屏的主要内容而不是收尾：一个项目几百条活里同时活着的往往只有几
 *  条，三列全空、底下一条「已完成 292」才是常态。所以「施工中」那一列还要多说一
 *  句下一步——一块空板本身说不出该做什么。 */
function emptyLine(column: BoardColumn): string {
  if (mine.value) return '暂无归你的活'
  return `暂无${columnLabel(column)}的活`
}

/** 每个房间在跑几条 —— 撞额度的那些，一眼看得出来。
 *
 *  数的是整块板，不是筛过的那一份：房间满没满和「谁的活」无关，按筛过的结果数会
 *  在开关一开的时候把「房间满员」凭空数没。 */
const runningPerRoom = computed(() => {
  const n = new Map<string, number>()
  for (const r of rows.value) {
    if (isRunning(r)) n.set(r.room_id, (n.get(r.room_id) ?? 0) + 1)
  }
  return n
})

const doneRows = computed(() => inColumn('done'))
/** 折叠行在不在，看的是整块板有没有已完成的活——不是筛过之后还剩几件。开关一开
 *  就把「已完成 292」整条抹掉，会读成「这个项目从来没交付过什么」。 */
const doneTotal = computed(() => totalByColumn.value.get('done')?.length ?? 0)

/** 顶上那行统计。以前是「N 件在跑 · N 件排队 · N 件等验收」，现在用板自己的词——
 *  三列的计数各自也在列头上，这一行是把它们和折起来的「已完成」并成一句。
 *
 *  数的是整块板，不是筛过的那一份：这一行说的是「这个项目有多少活」，筛不筛是看
 *  的人此刻的取景，两件事。列头上那个「3 / 12」才是取景的结果。 */
const tally = computed(() => {
  const all = totalByColumn.value
  return [
    ...BOARD_COLUMNS.map((c) => ({ label: c.label, n: all.get(c.key)?.length ?? 0 })),
    { label: '已完成', n: all.get('done')?.length ?? 0 },
  ].filter((t) => t.n > 0)
})

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
      <div class="board__head-row">
        <h1 class="t-title">看板</h1>
        <!-- 「只看我的」：一个项目上百个房间，「等你」那一列里大部分不是等你。
             登录身份取不到时不画这个开关——按空 handle 筛只会把整块板清空。 -->
        <button v-if="mineHandle" type="button" class="board__mine t-meta" :aria-pressed="mine" @click="toggleMine">
          <span class="board__sw" aria-hidden="true" />
          只看我的
        </button>
      </div>
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

    <div v-if="errorMsg" class="pa-6 t-body c-muted">
      {{ errorMsg }}
      <v-btn class="ms-2" size="small" variant="text" @click="load()">重试</v-btn>
    </div>

    <template v-else>
      <!-- 列永远都在，空了也留着列头和 0。整列消失会让板在两次刷新之间跳，而位置
           本身就是信息：「等你」那一列在哪儿，不该取决于它此刻有没有东西。筛选也
           一样——「只看我的」筛空一列，那一列照样留在原地。 -->
      <div class="board__cols">
        <section v-for="col in BOARD_COLUMNS" :key="col.key" class="board-col" :data-column="col.key">
          <header class="board-col__head">
            <span class="board-dot" :class="col.cls" :style="columnDotStyle(col.key)" aria-hidden="true" />
            <span class="board-col__name t-body">{{ col.label }}</span>
            <span class="board-col__count t-meta">{{ countLabel(col.key) }}</span>
          </header>
          <!-- 活还在路上时，列已经在这儿了：列本身是固定的（三列 + 列头），会变的
               只有里面装什么。所以加载态画在列**里面**，板的框架一开始就是最终的
               样子，卡到齐的那一刻没有任何东西挪位置。定时重拉走的是静默那一路，
               它不碰 loading，所以骨架不会在人看着的时候再回来一次。 -->
          <LoadingSkeleton v-if="loading && !rows.length" variant="entry" :rows="2" class="board-col__skel" />
          <ul v-else class="board-col__list">
            <!-- 空列自己说它空。「施工中」那一列还多一句下一步：三列同时空着是这
                 个项目的常态，那几行字就是第一屏的主要内容。 -->
            <li v-if="!inColumn(col.key).length" class="board-col__empty t-body">
              {{ emptyLine(col.key) }}
              <span v-if="col.key === 'building' && !mine" class="board-col__next t-meta"
                >在房间里说一声，芝士会把它拆成活</span
              >
            </li>
            <li v-for="row in inColumn(col.key)" :key="row.id">
              <button type="button" class="board-card" @click="openTask(row)">
                <span class="board-card__title t-body">{{ row.title }}</span>
                <span class="board-card__owner t-meta">
                  <span class="board-card__room">{{ roomTitle(row.room_id) }}</span>
                  <span class="board-card__sep">·</span>
                  <span v-if="row.owner_handle" class="board-card__who">
                    <img
                      v-if="avatarSrc(row.owner_handle)"
                      class="board-card__avatar"
                      :src="avatarSrc(row.owner_handle)!"
                      alt=""
                      @error="onAvatarError(row.owner_handle)"
                    />
                    <span
                      v-else
                      class="board-card__avatar board-card__avatar--initial"
                      :style="{ backgroundColor: avatarColor(row.owner_handle) }"
                      aria-hidden="true"
                      >{{ avatarInitial(ownerName(row.owner_handle)) }}</span
                    >
                    <span class="board-card__name">{{ ownerName(row.owner_handle) }}</span>
                  </span>
                  <span v-else class="c-faint">暂无负责人</span>
                  <!-- 这个房间四个槽位占满了：它后面那些是真的在等，不是没人理。 -->
                  <span v-if="(runningPerRoom.get(row.room_id) ?? 0) >= 4" class="board-card__full">房间满员</span>
                </span>
                <span class="board-card__rule" aria-hidden="true" />
                <span class="board-card__status t-meta">
                  <!-- 在跑的那条活换成侧栏那一颗呼吸点（同一个类名、同一套观感）：
                       色点是列级的，同一列里在跑的和排队的原来长得一模一样。 -->
                  <span v-if="isRunning(row)" class="running-dot" title="正在运行" />
                  <span v-else class="board-dot" :style="columnDotStyle(row.presentation.column)" aria-hidden="true" />
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
      <div v-if="doneTotal" class="board__done">
        <button type="button" class="board__done-head" :aria-expanded="showDone" @click="showDone = !showDone">
          <v-icon size="16">{{ showDone ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          <span class="t-body">{{ columnLabel('done') }}</span>
          <span class="t-meta board-col__count">{{ countLabel('done') }}</span>
        </button>
        <ul v-if="showDone" class="board__done-list">
          <li v-if="!doneRows.length" class="board-col__empty t-body">暂无归你的活</li>
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
.board__head-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 统计那一行在活到齐之前是空的，但位置得留着：一个空 <p> 高度为 0，字一出现整块
   板就往下掉一行。 */
.board__head p {
  min-height: 19px;
}
.board__sep {
  color: var(--faint);
  margin: 0 4px;
}
/* 「只看我的」。开着的时候整个开关变琥珀色——板上的每个计数都因此换了含义，这个
   状态不能是要找才看得见的。过渡只写具体属性，不写 all。 */
.board__mine {
  flex: none;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;
}
.board__mine:hover {
  background: var(--fill);
}
.board__mine[aria-pressed='true'] {
  border-color: var(--accent);
  color: var(--accent-ink);
  background: var(--accent-wash);
}
/* 拨柄。位置变化留给真的发生了变化的时刻——按下开关就是那种时刻。 */
.board__sw {
  position: relative;
  width: 26px;
  height: 15px;
  border-radius: var(--radius-pill);
  background: var(--line-2);
  transition: background-color 0.15s ease;
}
.board__sw::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--surface);
  transition: transform 0.15s ease;
}
.board__mine[aria-pressed='true'] .board__sw {
  background: var(--accent);
}
.board__mine[aria-pressed='true'] .board__sw::after {
  transform: translateX(11px);
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
/* 骨架顶掉的是 ul，所以它得自己补上那圈 8px —— 骨头自带左右各 8px 的外边距，
   卡片的 10px 内边距由骨架那边的 .skel__entry 出。 */
.board-col__skel {
  padding-block: 8px;
}

.board-col__empty {
  padding: 8px 4px;
  color: var(--muted);
  line-height: 1.7;
}
.board-col__next {
  display: block;
  color: var(--faint);
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
/* 悬停只改颜色，不改位置：一列几十张卡，鼠标扫过时每张抬一下会让整列跳。 */
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
.board-card__who {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.board-card__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board-card__avatar {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-pill);
  object-fit: cover;
}
.board-card__avatar--initial {
  display: flex;
  align-items: center;
  justify-content: center;
  /* stylelint-disable-next-line color-no-hex -- 压在头像底色上的墨色：底色是
     avatarColor() 按固定 OKLCH 亮度算出来的，两套主题下同一个值，所以字也不该跟
     着主题变。换成 token 会在深色下变成浅灰压浅底。见 design-system §唯一的例外。 */
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
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
/* 这会儿真的在跑的那条活，用的是侧栏那一颗呼吸点：逐条抄自 `TopicSidebar.vue` 的
   `.running-dot`（6px、实心 --ok、1.6s、缩到 0.7、淡到 0.45）。两处说的是同一件事
   「芝士正在动」，观感必须一致；抽不出来共用是因为 scoped 样式进不了别的组件，改
   这里记得改那里。 */
.running-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  animation: running-dot-pulse 1.6s ease-in-out infinite;
}
@keyframes running-dot-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.7);
  }
}
/* 全局那条把动画时长压到 0.001ms 的兜底对无限循环不够用（它只是让一圈瞬间跑完，
   然后无限重来），所以这里整个关掉，同 `common/LoadingSkeleton.vue` 的处理。关掉
   之后「在跑」照样认得出来：它是一颗实心小点，旁边几张是空心的圈。 */
@media (prefers-reduced-motion: reduce) {
  .running-dot {
    animation: none;
  }
}
</style>
