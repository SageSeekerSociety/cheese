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
// 取数、那一周的话题、删一条笔记，分别在同目录 `profile/` 下的三个 composable 里；
// 这一页自己的样式在 `profile/profile.css`。
import type { ProfileProject, ProfileProjectRole } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { useProfileNotes } from './profile/useProfileNotes'
import { useProfilePage } from './profile/useProfilePage'
import { useProfileTopics } from './profile/useProfileTopics'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AppPage from '@/components/common/AppPage.vue'
import ExternalTag from '@/components/common/ExternalTag.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import ActivityHeatmap from '@/components/profile/ActivityHeatmap.vue'
import { t } from '@/i18n'
import { label, NOTIF_KIND, TOPIC_STATUS } from '@/labels'
import { activityWeeks, HALF_YEAR_WEEKS } from '@/lib/activityYear'
import { teammateName } from '@/lib/agentNames'
import { agentDmKey } from '@/lib/dm'
import { relTime } from '@/lib/relTime'
import { skillIcon } from '@/lib/skillIcons'
import { topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'

defineOptions({ name: 'ProfileView' })

const props = defineProps<{ handle: string; projectId?: string }>()

const { mdAndUp } = useDisplay()
const compact = computed(() => !mdAndUp.value)
const isSelf = computed(() => myHandle() === props.handle)

const {
  data,
  pageKey,
  loading,
  error,
  refresh,
  profile,
  member,
  missing,
  remark,
  originalName,
  displayName,
  saveRemark,
  avatarUrl,
  locale,
  joined,
} = useProfilePage(
  () => props.handle,
  () => props.projectId
)

const editingRemark = ref(false)
const remarkDraft = ref('')
const remarkSaving = ref(false)
const remarkError = ref<string | null>(null)
watch(
  () => props.handle,
  () => {
    editingRemark.value = false
    remarkError.value = null
  }
)
function startRemarkEdit() {
  remarkDraft.value = remark.value
  remarkError.value = null
  editingRemark.value = true
}
function cancelRemarkEdit() {
  editingRemark.value = false
  remarkError.value = null
}
async function submitRemark(nextValue?: string | Event) {
  if (remarkSaving.value) return
  const raw = typeof nextValue === 'string' ? nextValue : remarkDraft.value
  remarkSaving.value = true
  remarkError.value = null
  try {
    await saveRemark(raw.trim())
    editingRemark.value = false
  } catch (e) {
    remarkError.value = e instanceof Error && e.message ? e.message : t('users.profile.remark.saveFailed')
  } finally {
    remarkSaving.value = false
  }
}
const { selectedWeek, weekPending, pickWeek, clearWeek, topicsTitle, topics, topicsFailed, topicsEmpty } =
  useProfileTopics(
    () => props.handle,
    () => data.value?.recent,
    () => locale.value
  )
const { forget } = useProfileNotes(
  () => pageKey.value,
  () => data.value
)

// ---- 活动 ----
const activityTotal = computed(() => {
  if (!compact.value) return profile.value?.activity.total ?? 0
  // 手机上那张图只有半年，数字按图上那几列加：周从星期一起，按天数截会差出几天。
  return activityWeeks(profile.value?.activity.days ?? [], HALF_YEAR_WEEKS).reduce((sum, w) => sum + w.total, 0)
})

// ---- 项目 ----
function roleLabel(role: ProfileProjectRole): string {
  if (role === 'agent') return t('users.profile.role.agent')
  return role === 'owner' ? t('users.profile.role.owner') : t('users.profile.role.team')
}

function projectStats(p: ProfileProject): string {
  // 手机上也写「发起 N 个话题」这一截：它是这个项目干了多少事的一半，窄屏没有
  // 理由把它省掉 —— 少了它，同一张卡片在两个宽度下说的不是同一件事。这一行放
  // 不下时自己折行（`.profile__row-text` 是 flex 列，里面的字没有不折行的规矩）。
  const parts = [t('users.profile.projects.stats', { topics: p.topics_started, count: p.contributions })]
  if (p.last_active_at) parts.push(t('users.profile.projects.active', { when: relTime(p.last_active_at) }))
  return parts.join(' · ')
}

/** 十二根柱子的高度（px）：按这个项目自己最忙的一周算满格，空的一周留一条底线。 */
function bars(weekly: number[]): number[] {
  const most = Math.max(1, ...weekly)
  return weekly.map((n) => (n === 0 ? 2 : Math.max(4, Math.round((n / most) * 24))))
}

function topicDot(status: string): string {
  return status === 'active' ? 'status-dot--ok' : 'status-dot--muted'
}

// ---- 在这个项目里 ----
const inProject = computed(() => (props.projectId ? member.value : null))
const roleInProject = computed(() => inProject.value?.source ?? null)
// 和项目里的另一位（人或 AI 队友）私聊。AI 队友的私聊按它自己的 handle 开，不是
// 名册上那个座位 handle（lib/dm.ts）。
const dmRoute = computed(() => {
  const here = inProject.value
  if (!props.projectId || !here || isSelf.value) return null
  const own = typeof here.instance_handle === 'string' ? here.instance_handle : props.handle
  const peer = here.source === 'agent' ? agentDmKey(own) : props.handle
  return { name: 'workspace-dm', params: { projectId: props.projectId, peer } }
})

/** 他名下那几个 AI 队友里，handle 对得上这一个。—— 人没有「带的技能」。
 *
 *  只画名字，不画数字：几颗是这个人自己的事，资料页只回答「是哪些」。
 *  也不画健康圆点：圆点得拿**项目**去探（`getSkillHealth(projectId, …)`），
 *  资料页不为看一眼技能多发一遍探针。 */
const carriedSkills = computed<string[]>(() => {
  // 名册里的链接用的是座位名（`seat_handle`，如 `cheese-c6cf7aefd1d5`），而
  // `/projects/{id}/agents` 的那份把两个名字都给了（`handle` 是 `cheese`）。对座位名
  // 才认得出是哪一个 —— 对 `handle` 永远对不上，这一格就永远不亮。
  const agent = (data.value?.agents ?? []).find((a) => a.seat_handle === props.handle)
  return agent?.configuration.skills ?? []
})
</script>

<template>
  <component
    :is="projectId ? AppPage : 'div'"
    v-bind="
      projectId
        ? {
            title: displayName,
            parent: {
              label: t('navigation.project.members'),
              to: { name: 'project-members', params: { projectId } },
            },
          }
        : { class: 'fill-height overflow-y-auto' }
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
        <BaseButton kind="secondary" @click="refresh">{{ t('users.profile.retry') }}</BaseButton>
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
              <div class="profile__name-row">
                <h1 class="t-page-title profile__name" data-user-content>{{ displayName }}</h1>
                <button
                  v-if="!isSelf && !editingRemark"
                  type="button"
                  class="profile__remark-btn"
                  data-testid="profile-remark-trigger"
                  :title="remark ? t('users.profile.remark.edit') : t('users.profile.remark.add')"
                  :aria-label="remark ? t('users.profile.remark.edit') : t('users.profile.remark.add')"
                  @click="startRemarkEdit"
                >
                  <v-icon size="14" icon="mdi-pencil-outline" aria-hidden="true" />
                  <span v-if="!remark">{{ t('users.profile.remark.add') }}</span>
                </button>
              </div>
              <div v-if="remark && originalName && originalName !== remark" class="t-meta-read profile__original-name">
                <span data-user-content>{{ t('users.profile.remark.originalName', { name: originalName }) }}</span>
                <span> · </span>
                <span class="profile__handle">{{ handle }}</span>
              </div>
              <div v-else class="t-meta-read profile__handle">{{ handle }}</div>
              <form v-if="!isSelf && editingRemark" class="profile__remark-form" @submit.prevent="submitRemark()">
                <input
                  v-model="remarkDraft"
                  type="text"
                  autocomplete="off"
                  maxlength="32"
                  class="profile__remark-input t-body"
                  data-testid="profile-remark-input"
                  :aria-label="t('users.profile.remark.placeholder')"
                  :placeholder="t('users.profile.remark.placeholder')"
                  :disabled="remarkSaving"
                  @keydown.esc.prevent="cancelRemarkEdit"
                />
                <div class="profile__remark-actions">
                  <BaseButton
                    type="submit"
                    size="sm"
                    :loading="remarkSaving"
                    data-testid="profile-remark-save"
                    @click="submitRemark()"
                  >
                    {{ t('users.profile.remark.save') }}
                  </BaseButton>
                  <BaseButton
                    v-if="remark"
                    type="button"
                    kind="ghost"
                    size="sm"
                    :disabled="remarkSaving"
                    data-testid="profile-remark-clear"
                    @click="submitRemark('')"
                  >
                    {{ t('users.profile.remark.clear') }}
                  </BaseButton>
                  <BaseButton type="button" kind="ghost" size="sm" :disabled="remarkSaving" @click="cancelRemarkEdit">
                    {{ t('users.profile.remark.cancel') }}
                  </BaseButton>
                </div>
                <p v-if="remarkError" class="t-meta profile__remark-error" role="alert">{{ remarkError }}</p>
              </form>
              <div v-if="roleInProject" class="profile__role">
                <ExternalTag v-if="roleInProject === 'external'" />
                <span v-else class="chip-neutral">{{ roleLabel(roleInProject) }}</span>
              </div>
            </div>
          </div>
          <p v-if="profile.bio" class="t-body-readable profile__bio" data-user-content>{{ profile.bio }}</p>
          <BaseButton v-if="isSelf" :to="{ name: 'UserSettingsProfile' }" kind="secondary" block class="profile__edit">
            {{ t('users.profile.edit') }}
          </BaseButton>
          <BaseButton
            v-else-if="dmRoute"
            :to="dmRoute"
            kind="secondary"
            prepend-icon="mdi-message-outline"
            block
            class="profile__edit"
          >
            {{ t('users.profile.dm') }}
          </BaseButton>
          <div class="profile__facts">
            <div class="profile__fact">
              <v-icon size="16" icon="mdi-calendar-blank-outline" aria-hidden="true" />
              <span>{{ joined }}</span>
            </div>
            <div v-if="profile.teams.length" class="profile__fact">
              <v-icon size="16" icon="mdi-account-multiple-outline" aria-hidden="true" />
              <!-- 一个团队一行：名字在行中间折开，读起来就分不清是一个团队还是两个。 -->
              <ul class="profile__teams" data-user-content>
                <li v-for="team in profile.teams" :key="team.id">
                  <router-link
                    v-if="team.handle"
                    :to="{ name: 'TeamsDetailDefault', params: { handle: team.handle } }"
                    >{{ team.name }}</router-link
                  >
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
            <!-- 带的技能：只有 AI 队友有名字可对。技能跟着人走，所以这一格挂在他身上，
                 不是挂在房间上（见 `useAgentSkills`）。只读：编辑在项目设置里。 -->
            <div
              v-if="carriedSkills.length"
              class="profile__card profile__card--pad profile__skills"
              data-section="carried-skills"
            >
              <div class="profile__card-head">
                <span class="t-eyebrow-read">{{ t('users.profile.inProject.skills') }}</span>
              </div>
              <ul class="profile__sockets">
                <li v-for="name in carriedSkills" :key="name" class="profile__socket" :title="name">
                  <span class="profile__socket-hole"
                    ><v-icon size="18">{{ skillIcon(name) }}</v-icon></span
                  >
                  <span class="profile__socket-name">{{ name }}</span>
                </li>
              </ul>
            </div>
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
                  <span class="t-body profile__ink" data-user-content>{{ w.title }}</span>
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
                  <span class="t-body profile__ink" :data-user-content="topic.title || undefined">{{
                    topicTitle(topic)
                  }}</span>
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
              <BaseEmptyState
                v-if="!profile.projects.length"
                size="inline"
                align="center"
                class="profile__empty"
                :title="isSelf ? t('users.profile.projects.emptyMine') : t('users.profile.projects.emptyShared')"
              />
              <router-link
                v-for="p in profile.projects"
                :key="p.project_id"
                class="profile__row profile__row--project"
                :to="{ name: 'workspace-project', params: { projectId: p.project_id } }"
              >
                <!-- 项目格子走 UserAvatar（kind="org" = 圆角方块，§3.14）：没挑过头像就退成项目名
                     首字母的底色方块。和左侧人像同一个组件、同一套退化，不再这里自己
                     截首字、自己上色。种子用 project_id —— 颜色跟着项目走，改名不换色。 -->
                <UserAvatar
                  :avatar="''"
                  :name="p.name"
                  :seed="p.project_id"
                  kind="org"
                  :size="32"
                  class="profile__tile"
                />
                <span class="profile__row-text">
                  <span class="profile__row-title">
                    <span class="profile__project-name" data-user-content>{{ p.name }}</span>
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
              <BaseEmptyState
                v-if="!profile.understanding.length"
                size="inline"
                align="center"
                class="profile__empty"
                :title="t('users.profile.notes.empty')"
              />
              <TransitionGroup tag="ul" name="note" class="profile__notes">
                <li v-for="note in profile.understanding" :key="note.id" class="profile__row profile__row--note">
                  <span class="profile__row-text">
                    <span class="t-body-readable">{{ note.content }}</span>
                    <span v-if="note.project_name || note.agent_handle || note.agent_name" class="t-meta-read">
                      <template v-if="note.project_name">{{ note.project_name }}</template>
                      <template v-if="note.project_name && (note.agent_handle || note.agent_name)"> · </template>
                      <UserRef
                        v-if="note.agent_handle || note.agent_name"
                        :handle="note.agent_handle"
                        :name="teammateName(note.agent_name, note.agent_name_source)"
                        :project-id="note.project_id"
                      />
                    </span>
                  </span>
                  <BaseButton
                    icon="mdi-close"
                    kind="ghost"
                    size="sm"
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
              <BaseEmptyState
                v-else-if="topicsEmpty"
                size="inline"
                align="center"
                class="profile__empty"
                :title="selectedWeek ? t('users.profile.topics.emptyWeek') : t('users.profile.topics.empty')"
              />
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
                    <span
                      class="t-body profile__ink profile__topic-title"
                      :data-user-content="topic.title || undefined"
                      >{{ topicTitle(topic) }}</span
                    >
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

<style scoped src="./profile/profile.css"></style>
