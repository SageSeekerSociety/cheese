<template>
  <!-- A document opens on its own page: it draws its own header, outside the team page frame. -->
  <router-view v-if="route.name === 'TeamsDocument' && teamData" />
  <DetailView
    v-else
    :team-data="teamData"
    :is-member="isMember"
    :not-found="notFound"
    :failed="failed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    :page-title="pageTitle"
    :page-width="pageWidth"
    :team-intro="teamIntro"
    :join="join"
    :settings="settings"
    @retry-load="retryLoad"
    @close-settings="closeSettings"
  >
    <router-view />
  </DetailView>
</template>

<script setup lang="ts">
// 团队详情外框：页头（团队名 / 这一页）+ 当前这一页。团队常去的两页（项目、资料库）
// 列在首页侧栏里这个团队的下面，其余的在「设置」那一层。画面在 `DetailView.vue`：这里留着
// 地址、取数、「加入」那条请求、设置有哪几栏和标签页上的标题。
import type { Team } from '@/types'

import { computed, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import DetailView from './DetailView.vue'

import { ApiError } from '@/api'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { closeOverlay } from '@/lib/backOut'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { pageBeforeSettings } from '@/lib/settingsReturn'
import { usePageTitleStore } from '@/stores/title'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const titles = usePageTitleStore()
const teamData = ref<Team>()
provide(teamDataInjectionKey, teamData)

const notFound = ref(false)
/** 这一次没读到的那个错。404 是「没有这个团队」，另走一条；其余是非空就画失败。 */
const loadError = ref<unknown>(null)
const isMember = computed(() => teamData.value?.joinStatus === 'member')
// 「不给你看」和「这次没读到」在这里判，画面只收结论：它不碰判据模块。
const failed = computed(() => loadError.value !== null)
const failureReason = computed(() => loadFailureReason(loadError.value))
const forbidden = computed(() => isForbidden(loadError.value))

const PAGE_TITLES: Record<string, string> = {
  TeamsDetailDefault: 'home.nav.teamProjects',
  TeamsDocument: 'home.nav.teamLibrary',
  TeamsDetailLibrary: 'home.nav.teamLibrary',
}

// 设置是盖在整个窗口上的一层，和个人、空间、项目的设置同一个外框（`TeamSettingsView`）。
// 「团队」一组是团队自己（基本信息、成员），「资源」一组是它用的东西（设备、模型、额度），
// 最后红字一项：所有者转让或解散，其他人退出。自己名下的设备、模型和额度在「个人设置」里，
// 这里一栏也不画，地址转过去。
const inSettings = computed(() => route.matched.some((record) => record.meta.settingsOverlay))
const pageTitle = computed(() =>
  inSettings.value ? t('home.nav.teamSettings') : t(PAGE_TITLES[String(route.name)] ?? 'home.nav.teamProjects')
)
// 没读到团队、或者还不是成员时，设置的几栏照旧落在团队页的外框里：由外框说没有这个团队、
// 不给看，或者给出「加入」。
const pageWidth = computed(() => (inSettings.value ? 'read' : 'full'))

const GROUPS = [
  {
    key: 'team',
    title: 'teams.settings.groups.team',
    items: [
      { name: 'TeamsDetailProfile', label: 'teams.settings.profile', icon: 'mdi-information-outline' },
      { name: 'TeamsDetailMembers', label: 'home.nav.teamMembers', icon: 'mdi-account-multiple-outline' },
    ],
  },
  {
    key: 'resources',
    title: 'teams.settings.groups.resources',
    items: [
      { name: 'TeamsDetailCompute', label: 'home.nav.teamCompute', icon: 'mdi-laptop' },
      { name: 'TeamsDetailModels', label: 'home.nav.teamModels', icon: 'mdi-creation-outline' },
      { name: 'TeamsDetailCredits', label: 'home.nav.teamCredits', icon: 'mdi-wallet-outline' },
    ],
  },
] as const

const settings = computed(() => {
  const team = teamData.value
  if (!team || !inSettings.value || !isMember.value || team.personal) return null
  const to = (name: string) => ({ name, params: { handle: team.handle } })
  const owner = team.role === 'OWNER'
  return {
    groups: [
      ...GROUPS.map((group) => ({
        key: group.key,
        title: t(group.title),
        items: group.items.map((item) => ({
          key: item.name,
          label: t(item.label),
          icon: item.icon,
          to: to(item.name),
        })),
      })),
      {
        key: 'leave',
        items: [
          {
            key: 'TeamsDetailLeave',
            label: t(owner ? 'teams.settings.transferAndDisband' : 'teams.settings.leave'),
            icon: owner ? 'mdi-delete-outline' : 'mdi-exit-to-app',
            to: to('TeamsDetailLeave'),
            danger: true,
          },
        ],
      },
    ],
    active: route.name === 'TeamsDetailSettings' ? null : String(route.name),
    backTo: to('TeamsDetailSettings'),
  }
})

// 自己名下的三栏住在个人设置里：旧链接（通知、额度提示里的「查看额度」）照旧打得开。
const PERSONAL: Record<string, string> = {
  TeamsDetailCompute: 'UserSettingsCompute',
  TeamsDetailModels: 'UserSettingsModels',
  TeamsDetailCredits: 'UserSettingsCredits',
}

// 桌面上目录一直在左边，没有单独的目录页：`settings` 自己的地址落到第一栏。
watch(
  [() => route.name, () => teamData.value, mdAndUp],
  ([name, team, desktop]) => {
    if (!team || !inSettings.value) return
    if (team.personal) {
      void router.replace({ name: PERSONAL[String(name)] ?? 'UserSettingsCompute' })
      return
    }
    if (name === 'TeamsDetailSettings' && desktop)
      void router.replace({ name: 'TeamsDetailProfile', params: { handle: team.handle } })
  },
  { immediate: true }
)

function closeSettings() {
  const handle = teamData.value?.handle ?? String(route.params.handle)
  closeOverlay(router, pageBeforeSettings({ name: 'TeamsDetailDefault', params: { handle } }))
}

const teamIntro = computed(() => teamData.value?.intro ?? '')

const fetchTeamData = async (handle: string) => {
  notFound.value = false
  // 上一次的失败不许留到这一次：重新问一次，屏幕上先干净。
  loadError.value = null
  try {
    const { team } = await TeamsApi.detailByHandle(handle)
    teamData.value = team
    // 标签页上写这个团队（自己名下就是自己的昵称），不写笼统的「团队」。
    titles.setDynamicTitle(team.name, 'TeamsDetail')
  } catch (error) {
    // 隐身小队对非成员就是 404：和不存在的小队说同一句话。
    if (error instanceof ApiError && error.status === 404) notFound.value = true
    // 其余的留在这里画出来。原来这里 `throw`，抛出去就没人接 —— 页面一片空白，
    // 而「没读到」和「没有这个团队」在屏幕上是同一幅画面。
    else loadError.value = error
  }
}

/** 失败画面上那颗「重试」：拿地址里现在这个 handle 再问一次。 */
const retryLoad = () => fetchTeamData(String(route.params.handle))

const join = async (message: string) => {
  const { team } = await TeamsApi.join(teamData.value!.id, { message: message || undefined })
  teamData.value = team
}

watch(
  () => route.params.handle,
  async (handle) => {
    // Same team under a new handle (just renamed): keep the page, no reload.
    if (typeof handle !== 'string' || handle.toLowerCase() === teamData.value?.handle.toLowerCase()) return
    teamData.value = undefined
    await fetchTeamData(handle)
  },
  { immediate: true }
)
</script>
