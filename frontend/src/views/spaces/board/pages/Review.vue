<script setup lang="ts">
// 审核队列。规则一变，这一屏的重点就变了：
//
// - 真平台今天审题走 `PATCH /tasks/{id}`，判据只有一句「在不在管理员名单里」
//   （`if not is_space_admin: raise ForbiddenError(...)`）。`tasks.py` 那两处
//   「出题者不可自审」的注释说的是「非管理员无权审」，**不是**对创建者的额外阻挡
//   —— 历史上确有一个 `is_creator` 版本，在 #1450（2026-09-22）被换成了现在这样。
// - 所以新规则早就成立：**所有者与管理员都能审任何一道待审题，包括自己出的**。
//   这一屏要显眼地说出「这道题是你自己出的，你可以直接过」，而不是把它藏起来。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'

import PageBar from '../components/PageBar.vue'
import PanelCard from '../components/PanelCard.vue'
import { type BoardTask, type ReviewedTask } from '../model'
import { deadlineText } from '../model'
import { loadPending, loadRecentReviews, me, reviewTask, space } from '../store'

import UserRef from '@/components/common/UserRefLink.vue'

/** 一页 20 道，与空间首页同一口径。队列积压时（开放发题之后就一定会积压）才翻页。 */
const PAGE_SIZE = 20

const route = useRoute()
const spaceId = computed(() => route.params.spaceId as string)

const pending = ref<BoardTask[]>([])
/** 最近处理过的那几道 —— 审核痕迹（谁、什么时候）只在这张卡上露面。 */
const recent = ref<ReviewedTask[]>([])
const busy = ref(false)
const rejectFor = ref<string | null>(null)
const reason = ref('')
const page = ref(1)

async function refresh() {
  const [queue, handled] = await Promise.all([loadPending(), loadRecentReviews()])
  pending.value = queue
  recent.value = handled
}

// 首屏两块都等空间到齐再取：审核人的名字要从这块板的管理员名册里对出来（真接口
// 只给 `reviewedBy` 那个 user id），而名册是外壳**异步**装进来的 —— 直接落在这条
// 路由上（刷新页面）时 setup 跑的那一刻它还没到。首页的待审队列是同一个理由，见
// `BoardHome.vue` 顶部那段。`space` 每次装板都会换成新对象，所以用一个 flag 把
// 「首屏」与后面那些 `refresh()` 分开，免得每审一道题都再取一遍。
let booted = false
watch(
  space,
  async (s) => {
    if (!s || booted) return
    booted = true
    await refresh()
  },
  { immediate: true }
)

/** 审核那一刻。同一天只给时分，更早的给出日期 —— 这一栏要的是「刚刚」还是「上周」。 */
function reviewedText(at: string) {
  const when = dayjs(at)
  return when.isSame(dayjs(), 'day') ? `今天 ${when.format('HH:mm')}` : when.format('MM-DD HH:mm')
}

const queue = computed(() =>
  [...pending.value].sort((a, b) => {
    // 自己出的排最前：能当场处理掉的先处理，队列才不会被自己堵住。
    const mineA = a.publisher.handle === me.value.handle ? 0 : 1
    const mineB = b.publisher.handle === me.value.handle ? 0 : 1
    if (mineA !== mineB) return mineA - mineB
    return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
  })
)

const pagedQueue = computed(() => queue.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 审掉一页里的题之后队列会变短：页数缩了就收回到最后一页，不要停在空页上。
watch(
  () => Math.max(1, Math.ceil(queue.value.length / PAGE_SIZE)),
  (last) => {
    if (page.value > last) page.value = last
  }
)

function setPage(next: number) {
  page.value = next
  document.querySelector('.queue')?.scrollIntoView({ block: 'start' })
}

async function doApprove(id: string) {
  busy.value = true
  try {
    await reviewTask(id, true)
    await refresh()
  } finally {
    busy.value = false
  }
}

function openReject(id: string) {
  rejectFor.value = id
  reason.value = ''
}

async function doReject() {
  if (!rejectFor.value || !reason.value.trim()) return
  busy.value = true
  try {
    await reviewTask(rejectFor.value, false, reason.value.trim())
    rejectFor.value = null
    await refresh()
  } finally {
    busy.value = false
  }
}

const nowHandling = computed(() => pending.value.find((t) => t.id === rejectFor.value))

function detailTo(id: string) {
  return { name: 'SpaceBoardTaskDetail', params: { spaceId: spaceId.value, taskId: id } }
}
</script>

<template>
  <div class="rev">
    <div class="rev__head">
      <div>
        <h1>审核</h1>
        <p>所有者和管理员可以审任何一道待审题，<b>包括自己出的那道</b>。不通过要写清原因，作者改完能重新提交。</p>
      </div>
      <v-chip variant="tonal" label>{{ pending.length }} 道待审</v-chip>
    </div>

    <PanelCard v-if="queue.length" title="待审核" subtitle="按「先审自己的、再按提交时间」排">
      <ul class="queue">
        <li v-for="task in pagedQueue" :key="task.id" class="queue__row">
          <div class="queue__main">
            <div class="queue__titleline">
              <router-link :to="detailTo(task.id)" class="queue__title">{{ task.title }}</router-link>
              <v-chip
                v-if="task.publisher.handle === me.handle"
                size="x-small"
                label
                variant="tonal"
                class="queue__self"
              >
                你自己出的 · 可直接通过
              </v-chip>
              <!-- 出处：队列是刚确认发布那批题的**唯一去处**（还没上板，列表里看
                   不到），而正文里那串 `【PDF · 第 N 页】` 已经被 `splitOrigin`
                   摘掉、变成题上的一枚标了 —— 所以审的人要看出处，就得看这里。
                   形状与 `components/TaskCard.vue` 那枚一致。 -->
              <v-chip
                v-if="task.origin"
                size="x-small"
                label
                variant="tonal"
                color="info"
                class="queue__origin"
                data-testid="queue-origin"
              >
                <v-icon icon="mdi-file-pdf-box" size="13" start />
                {{ task.origin }}
              </v-chip>
            </div>
            <p class="queue__summary">{{ task.summary }}</p>
            <div class="queue__meta">
              <span>作者 <UserRef :handle="task.publisher.handle" :name="task.publisher.name" /></span>
              <span v-if="task.category">{{ task.category }}</span>
              <span>{{ task.participantLimit === null ? '领取不限' : `领取上限 ${task.participantLimit}` }}</span>
              <span>{{
                task.minTeamSize === 1 && task.maxTeamSize === 1
                  ? '单人'
                  : `小队 ${task.minTeamSize}–${task.maxTeamSize}`
              }}</span>
              <span>{{ deadlineText(task) }}</span>
            </div>
          </div>
          <div class="queue__actions">
            <v-btn variant="text" size="small" :to="detailTo(task.id)">查看</v-btn>
            <v-btn variant="tonal" size="small" color="error" :disabled="busy" @click="openReject(task.id)">驳回</v-btn>
            <v-btn variant="flat" size="small" color="primary" :loading="busy" @click="doApprove(task.id)"
              >通过上板</v-btn
            >
          </div>
        </li>
      </ul>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="queue.length" @update:page="setPage" />
    </PanelCard>

    <v-empty-state v-else icon="mdi-check-all" title="队列是空的" text="没有待审的题。有新题提交时会出现在这里。" />

    <PanelCard title="最近处理过" subtitle="最近审过的题、是谁审的、什么时候" class="rev__recent">
      <ul v-if="recent.length" class="recent">
        <li v-for="item in recent" :key="item.id" class="recent__row">
          <v-icon
            :icon="item.result === 'APPROVED' ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'"
            :color="item.result === 'APPROVED' ? 'success' : 'error'"
            size="16"
          />
          <router-link :to="detailTo(item.id)" class="recent__title">{{ item.title }}</router-link>
          <span class="recent__result">{{ item.result === 'APPROVED' ? '通过' : '驳回' }}</span>
          <span class="recent__by">
            <template v-if="item.reviewer">
              <UserRef :handle="item.reviewer.handle" :name="item.reviewer.name" /> 审
            </template>
            <template v-else>不知是谁审的</template>
          </span>
          <time class="recent__at" :datetime="item.reviewedAt">{{ reviewedText(item.reviewedAt) }}</time>
        </li>
      </ul>
      <p v-else class="recent__empty">暂无处理记录</p>
    </PanelCard>

    <v-dialog :model-value="rejectFor !== null" max-width="520" @update:model-value="rejectFor = null">
      <v-card rounded="lg" class="pa-5">
        <h3 class="text-body-1 font-weight-bold mb-2">驳回「{{ nowHandling?.title }}」</h3>
        <p class="text-body-2 text-medium-emphasis mb-3">
          原因会原样发给作者。写清「哪里不合格、改成什么样」，作者就能直接改，不用来回问。
        </p>
        <v-textarea
          v-model="reason"
          autocomplete="off"
          variant="outlined"
          rows="4"
          auto-grow
          placeholder="比如：口径没写清，先定义清楚再提交。"
        />
        <div class="d-flex justify-end ga-2 mt-4">
          <v-btn variant="text" @click="rejectFor = null">取消</v-btn>
          <v-btn color="error" variant="flat" :disabled="!reason.trim()" :loading="busy" @click="doReject">驳回</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.rev__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.rev__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.rev__head p {
  max-width: 640px;
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
  line-height: 1.7;
}

.queue {
  padding: 0;
  margin: 0;
  list-style: none;
}

.queue__row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.queue__row:first-child {
  border-top: none;
  padding-top: 0;
}

.queue__main {
  min-width: 0;
}

.queue__titleline {
  display: flex;
  gap: 10px;
  align-items: center;
}

.queue__title {
  font-size: 0.94rem;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.queue__title:hover {
  text-decoration: underline;
}

.queue__self {
  color: rgb(var(--v-theme-primary));
}

.queue__summary {
  margin: 6px 0 8px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
  line-height: 1.6;
}

.queue__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.queue__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
}

.rev__recent {
  margin-top: 16px;
}

.recent {
  padding: 0;
  margin: 0;
  list-style: none;
}

.recent__row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 0;
  font-size: 0.8rem;
  border-top: 1px solid var(--line);
}

.recent__row:first-child {
  border-top: none;
  padding-top: 0;
}

.recent__title {
  overflow: hidden;
  font-weight: 600;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}

.recent__title:hover {
  text-decoration: underline;
}

.recent__result {
  flex: 0 0 auto;
  color: var(--muted);
}

/* 审核人与时间靠右、同一列起 —— 一列里的这几行要能竖着扫下来。 */
.recent__by {
  margin-left: auto;
  color: var(--faint);
  white-space: nowrap;
}

.recent__at {
  flex: 0 0 auto;
  color: var(--faint);
  white-space: nowrap;
}

.recent__empty {
  margin: 0;
  color: var(--faint);
  font-size: 0.8rem;
}
</style>
