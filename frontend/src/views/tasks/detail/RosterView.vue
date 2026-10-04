<template>
  <!-- 「领取者」页签的画面：批领取申请、评审提交、设截止、看逐版提交，都在这一张表里，按状态筛。
       取数和写操作在 `Roster.vue`，这里只报「对谁做什么」。 -->
  <div class="rs">
    <div class="rs__bar">
      <div v-roving-tabs class="rs__seg" role="tablist" :aria-label="t('tasks.roster.filterLabel')">
        <button
          v-for="f in filters"
          :key="f.key"
          type="button"
          role="tab"
          class="rs__seg-item"
          :class="{ 'rs__seg-item--on': filter === f.key }"
          :aria-selected="filter === f.key"
          @click="filter = f.key"
        >
          {{ f.label }}<span class="t-num">{{ f.count }}</span>
        </button>
      </div>
      <label class="rs__search">
        <v-icon size="16">mdi-magnify</v-icon>
        <input v-model="query" type="search" :placeholder="t('tasks.roster.search')" autocomplete="off" />
      </label>
    </div>

    <p v-if="loading" class="rs__note">{{ t('tasks.roster.loading') }}</p>
    <p v-else-if="denied" class="rs__note">{{ t('tasks.roster.denied') }}</p>
    <BaseEmptyState
      v-else-if="!visible.length"
      size="inline"
      class="rs__note"
      :title="rows.length ? t('tasks.roster.noMatch') : t('tasks.roster.empty')"
    />

    <BaseTable v-else class="rs__grid" :cols="ROSTER_COLS" :label="t('tasks.roster.tableLabel')" min-width="760px">
      <template #head>
        <tr>
          <BaseTableTh>{{ t('tasks.roster.col.who') }}</BaseTableTh>
          <BaseTableTh>{{ t('tasks.roster.col.claimedAt') }}</BaseTableTh>
          <BaseTableTh>{{ t('tasks.roster.col.status') }}</BaseTableTh>
          <BaseTableTh>{{ t('tasks.roster.col.latest') }}</BaseTableTh>
          <BaseTableTh>{{ t('tasks.roster.col.deadline') }}</BaseTableTh>
          <BaseTableTh>
            <span class="rs__sr">{{ t('tasks.roster.col.ops') }}</span>
          </BaseTableTh>
        </tr>
      </template>
      <template v-for="row in visible" :key="row.id">
        <tr
          class="rs__row"
          :data-status="row.status"
          @contextmenu="row.approved === 'APPROVED' && rowMenu.open(row.id, $event)"
        >
          <td>
            <button
              type="button"
              class="rs__who"
              :aria-expanded="expanded === row.id"
              :disabled="!row.hasDetails"
              @click="expanded = expanded === row.id ? null : row.id"
            >
              <span class="rs__avatar" :class="{ 'rs__avatar--team': row.teamSize }">{{ row.name.slice(0, 1) }}</span>
              <span class="rs__name">{{ row.name }}</span>
              <small v-if="row.teamSize" class="rs__small">{{ t('tasks.roster.teamSize', { n: row.teamSize }) }}</small>
              <v-icon v-if="row.hasDetails" size="14" class="rs__chev">
                {{ expanded === row.id ? 'mdi-chevron-up' : 'mdi-chevron-down' }}
              </v-icon>
            </button>
          </td>
          <td class="rs__meta t-num">{{ day(row.claimedAt) }}</td>
          <td>
            <span class="rs__status" :class="`rs__status--${STATUS[row.status].tone}`">
              {{ t(STATUS[row.status].label) }}
            </span>
          </td>
          <td>
            <template v-if="row.latest">{{
              t('tasks.roster.latest', { n: row.latest.version, when: when(row.latest.createdAt) })
            }}</template>
            <span v-else class="rs__meta">—</span>
          </td>
          <td class="rs__meta t-num">{{ row.deadline ? day(row.deadline) : '—' }}</td>
          <td>
            <div class="rs__ops">
              <template v-if="row.status === 'CLAIM_PENDING'">
                <BaseButton kind="primary" size="sm" :loading="busyId === row.id" @click="emit('approve', row.id)">
                  {{ t('tasks.roster.approve') }}
                </BaseButton>
                <BaseButton kind="ghost" size="sm" @click="openReject(row)">{{ t('tasks.roster.reject') }}</BaseButton>
              </template>
              <BaseButton
                v-else-if="row.status === 'REVIEW_PENDING'"
                kind="primary"
                size="sm"
                @click="emit('review', { id: row.id, name: row.name })"
              >
                {{ t('tasks.roster.review') }}
              </BaseButton>
              <BaseButton
                v-else-if="row.latest"
                kind="ghost"
                size="sm"
                @click="emit('review', { id: row.id, name: row.name })"
              >
                {{ t('tasks.roster.view') }}
              </BaseButton>
              <AdaptiveMenu v-if="row.approved === 'APPROVED'" v-bind="rowMenu.bind(row.id)" :actions="rowActions(row)">
                <template #activator="{ props: menu }">
                  <BaseButton v-bind="menu" icon="mdi-dots-horizontal" size="sm" :aria-label="t('tasks.roster.more')" />
                </template>
              </AdaptiveMenu>
            </div>
          </td>
        </tr>
        <tr v-if="expanded === row.id" class="rs__detail">
          <td colspan="6">
            <dl>
              <div v-if="row.m.realNameInfo?.realName">
                <dt>{{ t('tasks.roster.detail.realName') }}</dt>
                <dd>{{ realNameLine(row.m.realNameInfo) }}</dd>
              </div>
              <div v-if="row.m.teamMembers?.length">
                <dt>{{ t('tasks.roster.detail.teamMembers') }}</dt>
                <dd>
                  <span v-for="(member, i) in row.m.teamMembers" :key="i" class="rs__member">
                    {{
                      member.isLeader
                        ? t('tasks.roster.detail.leader', { name: memberName(member) })
                        : memberName(member)
                    }}
                  </span>
                </dd>
              </div>
              <div v-if="row.m.phone">
                <dt>{{ t('tasks.roster.detail.phone') }}</dt>
                <dd>{{ row.m.phone }}</dd>
              </div>
              <div v-if="row.m.email">
                <dt>{{ t('tasks.roster.detail.email') }}</dt>
                <dd>{{ row.m.email }}</dd>
              </div>
              <div v-if="row.m.applyReason">
                <dt>{{ t('tasks.roster.detail.applyReason') }}</dt>
                <dd>{{ row.m.applyReason }}</dd>
              </div>
              <div v-if="row.m.personalAdvantage">
                <dt>
                  {{
                    row.teamSize ? t('tasks.roster.detail.teamAdvantage') : t('tasks.roster.detail.personalAdvantage')
                  }}
                </dt>
                <dd>{{ row.m.personalAdvantage }}</dd>
              </div>
            </dl>
          </td>
        </tr>
      </template>
    </BaseTable>

    <AdaptiveDialog
      v-model="deadlineOpen"
      :title="t('tasks.roster.deadlineTitle')"
      :primary-label="t('tasks.roster.save')"
      :primary-disabled="!deadlineValue"
      size="sm"
      @primary="saveDeadline"
    >
      <p class="rs__dialog-lead">{{ t('tasks.roster.deadlineFor', { name: selected?.name ?? '' }) }}</p>
      <v-text-field
        v-model="deadlineValue"
        type="datetime-local"
        variant="outlined"
        density="comfortable"
        :min="minDeadline"
        hide-details
      />
    </AdaptiveDialog>

    <AdaptiveDialog
      v-model="rejectOpen"
      :title="t('tasks.roster.rejectTitle')"
      :primary-label="t('tasks.roster.reject')"
      primary-danger
      size="sm"
      @primary="confirmReject"
    >
      <p class="rs__dialog-lead">{{ t('tasks.roster.rejectLead', { name: selected?.name ?? '' }) }}</p>
      <v-textarea
        v-model="rejectReason"
        autocomplete="off"
        :label="t('tasks.roster.rejectReasonLabel')"
        variant="outlined"
        rows="3"
        counter="200"
        maxlength="200"
      />
    </AdaptiveDialog>
  </div>
</template>

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type {
  Task,
  TaskMembership,
  TaskParticipantRealNameInfo,
  TaskSubmissionReview,
  TaskTeamParticipantMemberSummary,
} from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import BaseTableTh from '@/components/base/BaseTableTh.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { vRovingTabs } from '@/lib/rovingTabs'

/** 一行的状态：先看领取申请批没批，批了再看最新那一版提交判没判。 */
type Status = 'CLAIM_PENDING' | 'CLAIM_REJECTED' | 'IN_PROGRESS' | 'REVIEW_PENDING' | 'PASSED' | 'FAILED'

// 第一列是谁来领的（名字、团队人数），吃剩下的宽度；其余定宽。
const ROSTER_COLS = [null, '104px', '96px', '170px', '104px', '150px']

const STATUS: Record<Status, { label: string; tone: string }> = {
  CLAIM_PENDING: { label: 'tasks.roster.claimPending', tone: 'muted' },
  CLAIM_REJECTED: { label: 'tasks.roster.rejected', tone: 'muted' },
  IN_PROGRESS: { label: 'tasks.roster.inProgress', tone: 'muted' },
  REVIEW_PENDING: { label: 'tasks.roster.reviewPending', tone: 'warn' },
  PASSED: { label: 'tasks.roster.passed', tone: 'ok' },
  FAILED: { label: 'tasks.roster.failed', tone: 'danger' },
}

export type Latest = { submissionId: number; version: number; createdAt: number; review?: TaskSubmissionReview }

type Row = {
  id: number
  m: TaskMembership
  name: string
  teamSize: number
  claimedAt: number
  deadline: number | null
  approved: TaskMembership['approved']
  status: Status
  latest: Latest | null
  hasDetails: boolean
}

const props = defineProps<{
  taskData: Task | null
  participants: TaskMembership[]
  /** participantId → 最新那一版提交。 */
  latestByParticipant: Map<number, Latest>
  loading: boolean
  /** 名单接口由服务端按 `may_teach_task` 把关；拿不到就说为什么，不画一张空表。 */
  denied: boolean
  /** 正在批准的那一行。 */
  busyId: number | null
}>()

const emit = defineEmits<{
  approve: [id: number]
  reject: [id: number, reason: string]
  deadline: [id: number, at: number]
  review: [who: { id: number; name: string }]
}>()

const { t } = useI18n()
const rowMenu = useRowMenu<number>()

function displayName(m: TaskMembership): string {
  if (props.taskData?.requireRealName && m.realNameInfo?.realName) return m.realNameInfo.realName
  return (
    m.member?.name ||
    (props.taskData?.submitterType === 'TEAM' ? t('tasks.roster.unknownTeam') : t('tasks.roster.unknownUser'))
  )
}

/** 实名那一行：姓名、学号、年级与班级，有几项写几项。 */
function realNameLine(info: TaskParticipantRealNameInfo): string {
  const cls = [info.grade && t('tasks.roster.detail.grade', { grade: info.grade }), info.className]
    .filter(Boolean)
    .join(' ')
  return [info.realName, info.studentId, cls].filter(Boolean).join(' · ')
}

function memberName(member: TaskTeamParticipantMemberSummary): string {
  if (props.taskData?.requireRealName && member.realNameInfo?.realName) {
    const id = member.realNameInfo.studentId
    return id
      ? t('tasks.roster.memberWithId', { name: member.realNameInfo.realName, id })
      : member.realNameInfo.realName
  }
  return member.name
}

function statusOf(m: TaskMembership, latest: Latest | undefined): Status {
  if (m.approved === 'NONE') return 'CLAIM_PENDING'
  if (m.approved === 'DISAPPROVED') return 'CLAIM_REJECTED'
  if (!latest) return 'IN_PROGRESS'
  // 还没判的那一版，接口回的是 `{ reviewed: false }` 而不是空：判没判只看 `reviewed`。
  if (!latest.review?.reviewed) return 'REVIEW_PENDING'
  return latest.review.detail.accepted ? 'PASSED' : 'FAILED'
}

/** 按领取时间倒序：最近领的在最上面。 */
const rows = computed<Row[]>(() =>
  [...props.participants]
    .sort((a, b) => b.createdAt - a.createdAt)
    .map((m) => {
      const latest = props.latestByParticipant.get(m.id)
      return {
        id: m.id,
        m,
        name: displayName(m),
        teamSize: m.teamMembers?.length ?? 0,
        claimedAt: m.createdAt,
        deadline: m.deadline,
        approved: m.approved,
        status: statusOf(m, latest),
        latest: latest ?? null,
        hasDetails: Boolean(
          m.realNameInfo?.realName ||
            m.teamMembers?.length ||
            m.phone ||
            m.email ||
            m.applyReason ||
            m.personalAdvantage
        ),
      }
    })
)

type FilterKey = 'all' | 'claim' | 'review' | 'passed' | 'rejected'
const filter = ref<FilterKey>('all')
const query = ref('')
const expanded = ref<number | null>(null)

const MATCH: Record<FilterKey, (r: Row) => boolean> = {
  // 「全部」是领取者：被拒绝的申请不算领了这道题，单独一格。
  all: (r) => r.status !== 'CLAIM_REJECTED',
  claim: (r) => r.status === 'CLAIM_PENDING',
  review: (r) => r.status === 'REVIEW_PENDING',
  passed: (r) => r.status === 'PASSED',
  rejected: (r) => r.status === 'CLAIM_REJECTED',
}

const filters = computed(() => {
  const count = (key: FilterKey) => rows.value.filter(MATCH[key]).length
  const list: { key: FilterKey; label: string; count: number }[] = [
    { key: 'all', label: t('tasks.roster.all'), count: count('all') },
    { key: 'claim', label: t('tasks.roster.claimPending'), count: count('claim') },
    { key: 'review', label: t('tasks.roster.reviewPending'), count: count('review') },
    { key: 'passed', label: t('tasks.roster.passed'), count: count('passed') },
  ]
  const rejected = count('rejected')
  if (rejected || filter.value === 'rejected')
    list.push({ key: 'rejected', label: t('tasks.roster.rejected'), count: rejected })
  return list
})

const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return rows.value.filter(
    (r) =>
      MATCH[filter.value](r) &&
      (!q || r.name.toLowerCase().includes(q) || (r.m.member?.name ?? '').toLowerCase().includes(q))
  )
})

function day(ms: number): string {
  return dayjs(ms).format(t('tasks.page.dateFormat'))
}

function when(ms: number): string {
  const d = dayjs(ms)
  if (d.isSame(dayjs(), 'day')) return t('tasks.roster.today', { time: d.format('HH:mm') })
  if (d.isSame(dayjs().subtract(1, 'day'), 'day')) return t('tasks.roster.yesterday', { time: d.format('HH:mm') })
  return d.format(t('tasks.roster.dateTimeFormat'))
}

// ── 拒绝与截止那两张对话框 ─────────────────────────────────────────────────────

const selected = ref<Row | null>(null)
const rejectOpen = ref(false)
const rejectReason = ref('')

function openReject(row: Row) {
  selected.value = row
  rejectReason.value = ''
  rejectOpen.value = true
}

function confirmReject() {
  if (!selected.value) return
  emit('reject', selected.value.id, rejectReason.value)
  rejectOpen.value = false
}

const deadlineOpen = ref(false)
const deadlineValue = ref('')
const minDeadline = computed(() => dayjs().format('YYYY-MM-DDTHH:mm'))

function rowActions(row: Row): MenuAction[] {
  return [
    {
      key: 'deadline',
      label: t('tasks.roster.setDeadline'),
      icon: 'mdi-calendar-clock-outline',
      onSelect: () => openDeadline(row),
    },
  ]
}

function openDeadline(row: Row) {
  selected.value = row
  deadlineValue.value = dayjs(row.deadline ?? dayjs().add(1, 'day').hour(23).minute(59)).format('YYYY-MM-DDTHH:mm')
  deadlineOpen.value = true
}

function saveDeadline() {
  if (!selected.value || !deadlineValue.value) return
  emit('deadline', selected.value.id, dayjs(deadlineValue.value).valueOf())
  deadlineOpen.value = false
}
</script>

<style scoped>
.rs__bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  margin-bottom: 12px;
}

.rs__seg {
  display: inline-flex;
  flex-wrap: wrap;
  padding: 2px;
  background: var(--fill-2);
  border-radius: var(--radius-md);
}

.rs__seg-item {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 3px 12px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
}

.rs__seg-item span {
  color: var(--faint);
  font-size: 12px;
}

.rs__seg-item--on {
  background: var(--surface);
  box-shadow: 0 0 0 1px var(--line);
  color: var(--ink);
  font-weight: 600;
}

.rs__search {
  display: flex;
  flex: 1;
  gap: 6px;
  align-items: center;
  min-width: 180px;
  max-width: 260px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  color: var(--faint);
}

.rs__search:focus-within {
  border-color: var(--muted);
}

.rs__search input {
  flex: 1;
  min-width: 0;
  color: var(--ink);
  font-size: 13px;
  outline: none;
}

.rs__note {
  margin: 24px 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

/* 表格壳是 BaseTable；这里只留名册自己的：不换行、正文色。 */
.rs__grid td {
  color: var(--text);
  font-size: 13px;
  white-space: nowrap;
}

.rs__who {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  color: var(--ink);
  text-align: left;
}

.rs__who:disabled {
  cursor: default;
}

.rs__avatar {
  display: grid;
  flex: none;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 50%;
  background: var(--fill-2);
  color: var(--text);
  font-size: 11px;
}
/* 团队来领的那一行不是一个人：圆角方块（形状照 GitHub 的规则，人才是圆的）。 */
.rs__avatar--team {
  border-radius: var(--radius-sm);
}

.rs__small,
.rs__chev,
.rs__meta {
  color: var(--faint);
}

.rs__status--muted {
  color: var(--muted);
}

.rs__status--warn {
  color: var(--warn-ink);
}

.rs__status--ok {
  color: var(--ok-ink);
}

.rs__status--danger {
  color: var(--danger-ink);
}

.rs__ops {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
}

.rs__grid .rs__detail td {
  padding: 4px 8px 12px 38px;
  background: var(--fill);
  white-space: normal;
}

.rs__detail dl {
  display: grid;
  gap: 4px;
  margin: 0;
}

.rs__detail dl > div {
  display: flex;
  gap: 12px;
}

.rs__detail dt {
  flex: none;
  width: 64px;
  color: var(--muted);
}

.rs__detail dd {
  margin: 0;
}

.rs__member + .rs__member::before {
  content: '、';
}

/* 顿号只在中文里是列表分隔符；英文界面用逗号。<html lang> 随界面语言切换。 */
:lang(en) .rs__member + .rs__member::before {
  content: ', ';
}

.rs__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.rs__dialog-lead {
  margin: 0 0 12px;
  color: var(--text);
}
</style>
