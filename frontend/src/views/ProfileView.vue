<script setup lang="ts">
// 个人主页：一个人是谁、这一年做了多少、在哪些项目里、最近参与了哪些话题。
//
// 一个页面，两个入口：
//   - 站内任何地方点一个人 → `/users/:handle`，全站的页面；
//   - 项目的成员名册里点一个人 → `/projects/:projectId/members/:handle`，留在项目
//     这个框里（页头「成员 / 名字」），右栏最前面多一段「在这个项目里」。
// 除此之外两个入口画的是同一页。
//
// 自己的主页多两样：「编辑资料」（去设置里的个人资料），和「芝士眼中的你」——各个
// 项目里的芝士记下的关于你的事，只有你看得到，可以一条条删掉。
//
// 活动图上点一格，下面的话题列表就只看那一周；再点一次同一周，或者点「回到最近」，
// 回到最近参与的那几个。
import type {
  MemberSummary,
  ProfileProject,
  ProfileProjectRole,
  ProfileTopic,
  ProfileUnderstanding,
  UserProfile,
} from '@/cx_types'
import type { ActivityWeek } from '@/lib/activityYear'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import { useCachedResource } from '@/composables/useCachedResource'
import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { usePageTitle } from '@/composables/usePageTitle'

import { deleteUnderstanding, getMemberSummary, getUserProfile, getUserTopics } from '@/api'
import ExternalTag from '@/components/common/ExternalTag.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ActivityHeatmap from '@/components/profile/ActivityHeatmap.vue'
import i18n, { t } from '@/i18n'
import { label, NOTIF_KIND, TOPIC_STATUS } from '@/labels'
import { formatUtcDay } from '@/lib/activityYear'
import { relTime } from '@/lib/relTime'
import { myHandle } from '@/me'
import ProjectPage from '@/views/workspace/ProjectPage.vue'

defineOptions({ name: 'ProfileView' })

const props = defineProps<{ handle: string; projectId?: string }>()

/** 「最近参与的话题」列几个；选中一周时列那一周的全部（接口上限 50）。 */
const RECENT_TOPICS = 8
const WEEK_TOPICS = 50

const { mdAndUp } = useDisplay()
const compact = computed(() => !mdAndUp.value)
const isSelf = computed(() => myHandle() === props.handle)

ensureDefaultAvatarId()

interface Page {
  profile: UserProfile
  member: MemberSummary | null
  /** 最近参与的话题；这一段单独失败时是 null，页面其余部分照常。 */
  recent: ProfileTopic[] | null
}

const { data, loading, error, refresh } = useCachedResource(
  () => (props.projectId ? `profile:${props.projectId}:${props.handle}` : `profile:${props.handle}`),
  async (): Promise<Page> => {
    const [profile, member, recent] = await Promise.all([
      getUserProfile(props.handle),
      props.projectId ? getMemberSummary(props.projectId, props.handle).catch(() => null) : Promise.resolve(null),
      getUserTopics(props.handle, { limit: RECENT_TOPICS })
        .then((r) => r.topics)
        .catch(() => null),
    ])
    return { profile, member, recent }
  }
)

const profile = computed(() => data.value?.profile ?? null)
const member = computed(() => data.value?.member ?? null)
// 平台上没有这个 handle 的账号：后端照样回一份空的主页，加入时间是空的。
const missing = computed(() => profile.value !== null && profile.value.joined_at === null)
const displayName = computed(() => profile.value?.name || props.handle)

const avatarUrl = computed(() => {
  const id = profile.value?.avatar_id
  return isChosenAvatar(id) ? getAvatarUrl(id!) : ''
})

const locale = computed(() => i18n.global.locale.value)
const joined = computed(() => {
  const at = profile.value?.joined_at
  if (!at) return ''
  const date = new Intl.DateTimeFormat(locale.value, { year: 'numeric', month: 'long' }).format(new Date(at))
  return t('users.profile.joined', { date })
})

// 顶栏（手机）和标签页标题写这个人的名字，而不是路由上那个泛泛的「成员」。
const { setDynamicTitle } = usePageTitle()
watch(
  () => (profile.value ? displayName.value : ''),
  (name) => {
    if (name) setDynamicTitle(name, props.projectId ? 'member' : 'UserPage')
  },
  { immediate: true }
)

// ---- 活动 ----
const activityTotal = computed(() => {
  const days = profile.value?.activity.days ?? []
  if (!compact.value) return profile.value?.activity.total ?? 0
  // 手机上那张图只有半年，数字跟着图走。
  return days.slice(-26 * 7).reduce((sum, d) => sum + d.count, 0)
})

// ---- 项目 ----
function roleLabel(role: ProfileProjectRole): string {
  return role === 'owner' ? t('users.profile.role.owner') : t('users.profile.role.team')
}

function projectStats(p: ProfileProject): string {
  const parts = compact.value
    ? [t('users.profile.projects.contributions', { count: p.contributions })]
    : [t('users.profile.projects.stats', { topics: p.topics_started, count: p.contributions })]
  if (p.last_active_at) parts.push(t('users.profile.projects.active', { when: relTime(p.last_active_at) }))
  return parts.join(' · ')
}

/** 十二根柱子的高度（px）：按这个项目自己最忙的一周算满格，空的一周留一条底线。 */
function bars(weekly: number[]): number[] {
  const most = Math.max(1, ...weekly)
  return weekly.map((n) => (n === 0 ? 2 : Math.max(4, Math.round((n / most) * 24))))
}

// ---- 话题：最近，或者选中的那一周 ----
const selectedWeek = ref<ActivityWeek | null>(null)
const weekTopics = ref<ProfileTopic[] | null>(null)
const weekPending = ref(false)
const weekFailed = ref(false)
let weekRequest = 0

function clearWeek() {
  weekRequest += 1
  selectedWeek.value = null
  weekTopics.value = null
  weekPending.value = false
  weekFailed.value = false
}

async function pickWeek(week: ActivityWeek) {
  if (selectedWeek.value?.from === week.from) {
    clearWeek()
    return
  }
  const seq = ++weekRequest
  selectedWeek.value = week
  weekPending.value = true
  weekFailed.value = false
  try {
    const { topics } = await getUserTopics(props.handle, { from: week.from, to: week.to, limit: WEEK_TOPICS })
    if (seq !== weekRequest) return
    weekTopics.value = topics
  } catch {
    if (seq !== weekRequest) return
    weekTopics.value = null
    weekFailed.value = true
  } finally {
    if (seq === weekRequest) weekPending.value = false
  }
}

// 换了一个人，上一个人选中的那一周不跟过来。
watch(() => props.handle, clearWeek)

const topicsTitle = computed(() => {
  const week = selectedWeek.value
  if (!week) return t('users.profile.topics.recent')
  const day = (d: string) => formatUtcDay(d, locale.value, { month: 'long', day: 'numeric' })
  return t('users.profile.topics.range', { from: day(week.from), to: day(week.to) })
})

// 选中一周、新的那一份还没回来时，列表先留着原来的几行（淡一点），不先清空再填满。
const topics = computed<ProfileTopic[]>(() => {
  if (selectedWeek.value && weekTopics.value) return weekTopics.value
  return data.value?.recent ?? []
})
const topicsFailed = computed(() => (selectedWeek.value ? weekFailed.value : data.value?.recent === null))
const topicsEmpty = computed(() => !weekPending.value && !topicsFailed.value && topics.value.length === 0)

function topicDot(status: string): string {
  return status === 'active' ? 'status-dot--ok' : 'status-dot--muted'
}

// ---- 芝士眼中的你 ----
function noteSource(note: ProfileUnderstanding): string {
  return [note.project_name, note.agent_name || note.agent_handle].filter(Boolean).join(' · ')
}

// 先从列表里拿掉，再去删；服务器不肯就放回原处。
async function forget(note: ProfileUnderstanding) {
  const notes = data.value?.profile.understanding
  if (!notes) return
  const at = notes.findIndex((n) => n.id === note.id)
  if (at < 0) return
  notes.splice(at, 1)
  try {
    await deleteUnderstanding(note.id)
  } catch {
    const now = data.value?.profile.understanding
    if (now && !now.some((n) => n.id === note.id)) now.splice(Math.min(at, now.length), 0, note)
    toast.error(t('users.profile.notes.deleteFailed'))
  }
}

// ---- 在这个项目里 ----
const inProject = computed(() => (props.projectId ? member.value : null))
const roleInProject = computed(() => inProject.value?.source ?? null)
</script>

<template>
  <component
    :is="projectId ? ProjectPage : 'div'"
    v-bind="
      projectId
        ? {
            title: displayName,
            parent: {
              label: t('navigation.project.members'),
              to: { name: 'project-members', params: { projectId } },
            },
          }
        : { class: 'profile-site fill-height overflow-y-auto' }
    "
  >
    <div class="profile" :class="{ 'profile--compact': compact, 'profile--in-project': !!projectId }">
      <!-- 加载中：和真内容同样的位置和大小，数据到了不跳。 -->
      <div v-if="loading" class="profile__grid" aria-busy="true">
        <div class="profile__id">
          <div class="skel skel--avatar" />
          <div class="skel skel--line skel--name" />
          <div class="skel skel--line" />
        </div>
        <div class="profile__main">
          <div class="skel skel--block skel--heatmap" />
          <div class="skel skel--block" />
        </div>
      </div>

      <div v-else-if="error" class="profile__state">
        <p class="t-body">{{ t('users.profile.loadFailed') }}</p>
        <v-btn variant="outlined" color="on-surface" @click="refresh">{{ t('users.profile.retry') }}</v-btn>
      </div>

      <div v-else-if="missing" class="profile__state">
        <p class="t-body">{{ t('users.profile.notFound') }}</p>
      </div>

      <div v-else-if="profile" class="profile__grid">
        <!-- 左栏：这个人是谁 -->
        <aside class="profile__id">
          <div class="profile__who">
            <UserAvatar :avatar="avatarUrl" :name="displayName" :size="compact ? 64 : 96" class="profile__avatar" />
            <div class="profile__names">
              <h1 class="t-page-title profile__name">{{ displayName }}</h1>
              <div class="t-meta-read profile__handle">@{{ handle }}</div>
              <div v-if="roleInProject" class="profile__role">
                <ExternalTag v-if="roleInProject === 'external'" />
                <span v-else class="chip-neutral">{{ roleLabel(roleInProject) }}</span>
              </div>
            </div>
          </div>
          <p v-if="profile.bio" class="t-body-readable profile__bio">{{ profile.bio }}</p>
          <v-btn
            v-if="isSelf"
            :to="{ name: 'UserSettingsProfile' }"
            variant="outlined"
            color="on-surface"
            block
            class="profile__edit"
          >
            {{ t('users.profile.edit') }}
          </v-btn>
          <div class="profile__facts">
            <div class="profile__fact">
              <v-icon size="16" icon="mdi-calendar-blank-outline" aria-hidden="true" />
              <span>{{ joined }}</span>
            </div>
            <div v-if="profile.teams.length" class="profile__fact">
              <v-icon size="16" icon="mdi-account-multiple-outline" aria-hidden="true" />
              <!-- 一个团队一行：名字在行中间折开，读起来就分不清是一个团队还是两个。 -->
              <ul class="profile__teams">
                <li v-for="team in profile.teams" :key="team.id">
                  <router-link v-if="team.handle" :to="{ name: 'TeamsDetail', params: { handle: team.handle } }">{{
                    team.name
                  }}</router-link>
                  <span v-else>{{ team.name }}</span>
                </li>
              </ul>
            </div>
          </div>
        </aside>

        <div class="profile__main">
          <!-- 在这个项目里（只在项目里打开时有） -->
          <section v-if="inProject" class="profile__section" data-section="in-project">
            <header class="profile__head">
              <h2 class="t-title">{{ t('users.profile.inProject.title') }}</h2>
              <span class="t-meta-read">{{
                t('users.profile.inProject.weekly', { count: inProject.weekly_contributions ?? 0 })
              }}</span>
            </header>
            <div class="profile__pair">
              <!-- 收件箱只有本人打得开，别人的主页上这一格永远是空的，所以只在自己的页上画。 -->
              <div v-if="isSelf" class="profile__card profile__card--pad">
                <div class="profile__card-head">
                  <span class="t-eyebrow-read">{{ t('users.profile.inProject.waiting') }}</span>
                  <span class="t-meta-read t-num">{{ inProject.waiting_on_you.length }}</span>
                </div>
                <p v-if="inProject.waiting_on_you.length === 0" class="t-body c-muted">
                  {{ t('users.profile.inProject.waitingEmpty') }}
                </p>
                <div v-for="w in inProject.waiting_on_you" :key="w.id" class="profile__waiting">
                  <span class="t-body profile__ink">{{ w.title }}</span>
                  <span class="t-meta-read">{{ label(NOTIF_KIND, w.kind) }}</span>
                </div>
              </div>
              <div class="profile__card profile__card--pad">
                <div class="profile__card-head">
                  <span class="t-eyebrow-read">{{ t('users.profile.inProject.active') }}</span>
                  <span class="t-meta-read t-num">{{ (inProject.topics_active ?? []).length }}</span>
                </div>
                <p v-if="!(inProject.topics_active ?? []).length" class="t-body c-muted">
                  {{ t('users.profile.inProject.activeEmpty') }}
                </p>
                <router-link
                  v-for="topic in inProject.topics_active ?? []"
                  :key="topic.id"
                  class="profile__active"
                  :to="{ name: 'workspace-topic', params: { projectId, topicId: topic.id } }"
                >
                  <span class="status-dot" :class="topicDot(topic.status)" />
                  <span class="t-body profile__ink">{{ topic.title }}</span>
                </router-link>
              </div>
            </div>
          </section>

          <!-- 活动 -->
          <section class="profile__section" data-section="activity">
            <header class="profile__head">
              <h2 class="t-title">{{ t('users.profile.activity.title') }}</h2>
              <span class="t-meta-read">{{
                compact
                  ? t('users.profile.activity.halfYearTotal', { count: activityTotal })
                  : t('users.profile.activity.yearTotal', { count: activityTotal })
              }}</span>
            </header>
            <div class="profile__card profile__card--pad">
              <ActivityHeatmap
                :days="profile.activity.days"
                :selected="selectedWeek?.from ?? null"
                :compact="compact"
                @select="pickWeek"
              />
            </div>
          </section>

          <!-- 项目 -->
          <section class="profile__section" data-section="projects">
            <header class="profile__head">
              <h2 class="t-title">
                {{ isSelf ? t('users.profile.projects.mine') : t('users.profile.projects.shared') }}
              </h2>
              <span v-if="profile.projects.length" class="t-meta-read t-num">{{ profile.projects.length }}</span>
            </header>
            <div class="profile__card">
              <p v-if="!profile.projects.length" class="profile__empty t-body">
                {{ isSelf ? t('users.profile.projects.emptyMine') : t('users.profile.projects.emptyShared') }}
              </p>
              <router-link
                v-for="p in profile.projects"
                :key="p.project_id"
                class="profile__row profile__row--project"
                :to="{ name: 'workspace-project', params: { projectId: p.project_id } }"
              >
                <span class="profile__tile" aria-hidden="true">{{ p.name.slice(0, 1) }}</span>
                <span class="profile__row-text">
                  <span class="profile__row-title">
                    <span class="profile__project-name">{{ p.name }}</span>
                    <ExternalTag v-if="p.source === 'external'" />
                    <span v-else class="chip-neutral">{{ roleLabel(p.source) }}</span>
                  </span>
                  <span class="t-meta-read">{{ projectStats(p) }}</span>
                </span>
                <span
                  v-if="!compact"
                  class="profile__spark"
                  role="img"
                  :aria-label="t('users.profile.projects.trend', { counts: p.weekly.join(', ') })"
                >
                  <span v-for="(h, i) in bars(p.weekly)" :key="i" :style="{ height: `${h}px` }" />
                </span>
              </router-link>
            </div>
          </section>

          <!-- 芝士眼中的你（只在自己的页上） -->
          <section v-if="isSelf" class="profile__section" data-section="notes">
            <header class="profile__head">
              <h2 class="t-title">{{ t('users.profile.notes.title') }}</h2>
              <span class="t-meta-read profile__private">
                <v-icon size="14" icon="mdi-lock-outline" aria-hidden="true" />
                {{ t('users.profile.notes.private') }}
              </span>
            </header>
            <div class="profile__card">
              <p v-if="!profile.understanding.length" class="profile__empty t-body">
                {{ t('users.profile.notes.empty') }}
              </p>
              <TransitionGroup tag="ul" name="note" class="profile__notes">
                <li v-for="note in profile.understanding" :key="note.id" class="profile__row profile__row--note">
                  <span class="profile__row-text">
                    <span class="t-body-readable">{{ note.content }}</span>
                    <span v-if="noteSource(note)" class="t-meta-read">{{ noteSource(note) }}</span>
                  </span>
                  <v-btn
                    icon="mdi-close"
                    variant="text"
                    color="on-surface-variant"
                    size="small"
                    :aria-label="t('users.profile.notes.delete')"
                    :title="t('users.profile.notes.delete')"
                    @click="forget(note)"
                  />
                </li>
              </TransitionGroup>
            </div>
          </section>

          <!-- 最近参与的话题 / 选中的那一周 -->
          <section class="profile__section" data-section="topics">
            <header class="profile__head">
              <h2 class="t-title">{{ topicsTitle }}</h2>
              <button v-if="selectedWeek" type="button" class="profile__link" @click="clearWeek">
                <span class="t-meta-read">{{ t('users.profile.topics.backToRecent') }}</span>
              </button>
            </header>
            <div class="profile__card" :aria-busy="weekPending" :class="{ 'profile__card--pending': weekPending }">
              <p v-if="topicsFailed" class="profile__empty t-body">{{ t('users.profile.topics.loadFailed') }}</p>
              <p v-else-if="topicsEmpty" class="profile__empty t-body">
                {{ selectedWeek ? t('users.profile.topics.emptyWeek') : t('users.profile.topics.empty') }}
              </p>
              <template v-else>
                <router-link
                  v-for="topic in topics"
                  :key="topic.id"
                  class="profile__row profile__row--topic"
                  :to="{ name: 'workspace-topic', params: { projectId: topic.project_id, topicId: topic.id } }"
                >
                  <span
                    class="status-dot"
                    :class="topicDot(topic.status)"
                    role="img"
                    :aria-label="label(TOPIC_STATUS, topic.status)"
                  />
                  <span class="profile__row-text">
                    <span class="t-body profile__ink profile__topic-title">{{ topic.title }}</span>
                    <span v-if="compact" class="t-meta-read">
                      {{ topic.project_name }} · {{ t('users.profile.topics.count', { count: topic.contributions }) }} ·
                      {{ relTime(topic.last_participated_at) }}
                    </span>
                  </span>
                  <template v-if="!compact">
                    <span class="t-meta-read profile__topic-project">{{ topic.project_name }}</span>
                    <span class="t-meta-read t-num profile__topic-count">{{
                      t('users.profile.topics.count', { count: topic.contributions })
                    }}</span>
                    <span class="t-meta profile__topic-when">{{ relTime(topic.last_participated_at) }}</span>
                  </template>
                </router-link>
              </template>
            </div>
          </section>
        </div>
      </div>
    </div>
  </component>
</template>

<style scoped>
.profile-site {
  background: var(--canvas);
}
.profile {
  --id-w: 232px;
  --col-gap: 48px;

  max-width: var(--page-w);
  margin-inline: auto;
  padding: 48px 16px;
}
/* 项目框里：ProjectPage 已经给了一栏 --page-w 和四周的边距，左栏窄一档。 */
.profile--in-project {
  --id-w: 200px;
  --col-gap: 40px;

  padding: 16px 0;
}
.profile--compact {
  padding: 24px 16px;
}
.profile--compact.profile--in-project {
  padding: 0;
}
.profile__grid {
  display: grid;
  grid-template-columns: var(--id-w) minmax(0, 1fr);
  align-items: start;
  column-gap: var(--col-gap);
}
.profile--compact .profile__grid {
  grid-template-columns: minmax(0, 1fr);
  row-gap: 32px;
}

/* ---- 左栏 ---- */
.profile__id {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.profile__who {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.profile--compact .profile__who {
  flex-direction: row;
  align-items: center;
}
.profile__names {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.profile__name {
  margin: 0;
  overflow-wrap: anywhere;
}
.profile__role {
  margin-top: 8px;
}
.profile__bio {
  margin: 0;
  overflow-wrap: anywhere;
}
.profile__facts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.profile--compact .profile__facts {
  flex-flow: row wrap;
  gap: 4px 16px;
  padding-top: 0;
  border-top: 0;
}
.profile__fact {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.profile__fact .v-icon {
  flex: none;
  margin-top: 2px;
}
.profile__teams {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}
.profile__teams a {
  color: inherit;
  text-decoration: none;
}
.profile__teams a:hover {
  color: var(--ink);
  text-decoration: underline;
}

/* ---- 右栏 ---- */
.profile__main {
  display: flex;
  flex-direction: column;
  gap: 32px;
  min-width: 0;
}
.profile__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.profile__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.profile__head h2 {
  margin: 0;
}
.profile__card {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  transition: opacity var(--dur-quick) var(--ease-standard);
}
.profile__card--pad {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}
.profile__card--pending {
  opacity: 0.6;
}
.profile__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.profile__pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}
.profile__ink {
  color: var(--ink);
}
.profile__waiting {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.profile__active {
  display: flex;
  align-items: center;
  gap: 8px;
  color: inherit;
  text-decoration: none;
}
.profile__active:hover .profile__ink {
  text-decoration: underline;
}
.profile__empty {
  margin: 0;
  padding: 24px 16px;
  color: var(--muted);
  text-align: center;
}

/* 列表行：项目、笔记、话题共用一种行 */
.profile__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.profile__row:first-child {
  border-top: 0;
}
a.profile__row:hover {
  background: var(--fill);
}
.profile__row--project {
  padding-block: 14px;
}
.profile--compact .profile__row {
  min-height: 44px;
}
.profile__row-text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.profile__row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.profile__project-name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profile__tile {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
.profile__spark {
  display: flex;
  flex: none;
  align-items: flex-end;
  gap: 2px;
  height: 24px;
}
.profile__spark span {
  width: 4px;
  background: var(--muted);
}
.profile__topic-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profile--compact .profile__topic-title {
  white-space: normal;
}
.profile--compact .profile__row--topic {
  align-items: flex-start;
}
.profile--compact .profile__row--topic .status-dot {
  margin-top: 7px;
}
.profile__row--topic .status-dot {
  flex: none;
}
.profile__topic-project {
  flex: none;
  max-width: 30%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profile__topic-count {
  flex: none;
  min-width: 64px;
  text-align: end;
  white-space: nowrap;
}
.profile__topic-when {
  flex: none;
  min-width: 72px;
  text-align: end;
  white-space: nowrap;
}
.profile__private {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.profile__link {
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}
.profile__link span {
  text-decoration: underline;
  text-underline-offset: 2px;
  transition: color var(--dur-quick) var(--ease-standard);
}
.profile__link:hover span {
  color: var(--ink);
}

/* 笔记：删掉的一条先淡出，下面的几条滑上来补位（§9.2）。 */
.profile__notes {
  position: relative;
  margin: 0;
  padding: 0;
  list-style: none;
}
.profile__row--note {
  align-items: flex-start;
  background: var(--surface);
}
.note-move {
  transition: transform var(--dur-base) var(--ease-standard);
}
.note-leave-active {
  position: absolute;
  inset-inline: 0;
  transition: opacity var(--dur-quick) var(--ease-in);
}
.note-leave-to {
  opacity: 0;
}
.note-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.note-enter-from {
  opacity: 0;
}

/* ---- 加载中、出错、查无此人 ---- */
.profile__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 64px 16px;
  color: var(--muted);
}
.profile__state p {
  margin: 0;
}
.skel {
  border-radius: var(--radius-md);
  background: var(--fill);
}
.skel--avatar {
  width: 96px;
  height: 96px;
  border-radius: var(--radius-pill);
}
.profile--compact .skel--avatar {
  width: 64px;
  height: 64px;
}
.skel--line {
  width: 60%;
  height: 18px;
}
.skel--name {
  width: 80%;
  height: 33px;
}
.skel--block {
  height: 160px;
  border-radius: var(--radius-lg);
}
.skel--heatmap {
  height: 200px;
}
</style>
