<template>
  <DetailView
    :team="teamData"
    :is-member="isMember"
    :not-found="notFound"
    :failed="loadFailed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    :page-title="pageTitle"
    :page-width="pageWidth"
    :team-intro="teamIntro"
    :join="join"
    :resolve-user="resolveUser"
    @retry="retryLoad"
    @navigate="navigate"
  />
</template>

<script setup lang="ts">
// 团队详情外框的**容器**：取数、读地址、读标题 store、人名与去处都在这儿；画的那一半
// 在 `DetailView.vue`。页头（团队名 / 这一页）+ 当前这一页。团队的几样东西（项目、
// 成员、知识库、工作电脑、额度）列在首页侧栏里这个团队的下面，这里不再自己画一条侧栏。
import type { Team } from '@/types'

import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import DetailView from './DetailView.vue'

import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'
import { usePageTitleStore } from '@/stores/title'

const route = useRoute()
const titles = usePageTitleStore()
const { resolve: resolveUser, navigate } = useUserRefResolver()
const teamData = ref<Team>()
provide(teamDataInjectionKey, teamData)

const notFound = ref(false)
/** 这一次没读到的那个错。404 是「没有这个团队」，另走一条；其余是非空就画失败。 */
const loadError = ref<unknown>(null)
const isMember = computed(() => teamData.value?.joinStatus === 'member')
const loadFailed = computed(() => loadError.value !== null)
// 是「不给你看」还是「这次没读到」，在这里判：画面只拿布尔值、那句话说，不认状态码。
const failureReason = computed(() => loadFailureReason(loadError.value))
const forbidden = computed(() => isForbidden(loadError.value))

const PAGE_TITLES: Record<string, string> = {
  TeamsDetailDefault: 'home.nav.teamProjects',
  TeamsDetailMembers: 'home.nav.teamMembers',
  TeamsDetailKnowledge: 'home.nav.teamKnowledge',
  TeamsDetailCompute: 'home.nav.teamCompute',
  TeamsDetailCredits: 'home.nav.teamCredits',
}
const pageTitle = computed(() => t(PAGE_TITLES[String(route.name)] ?? 'home.nav.teamProjects'))

// 成员和额度是读的一栏，页头跟着正文封顶居中；其余几页铺满内容区。
const READ_PAGES = new Set(['TeamsDetailMembers', 'TeamsDetailCredits'])
const pageWidth = computed<'read' | 'full'>(() => (READ_PAGES.has(String(route.name)) ? 'read' : 'full'))

const teamIntro = computed(() => teamData.value?.intro ?? '')

const fetchTeamData = async (handle: string) => {
  notFound.value = false
  // 上一次的失败不许留到这一次：重新问一次，屏幕上先干净。
  loadError.value = null
  try {
    const {
      data: { team },
    } = await TeamsApi.detailByHandle(handle)
    teamData.value = team
    // 标签页上写这个团队（自己名下就是自己的昵称），不写笼统的「团队」。
    titles.setDynamicTitle(team.name, 'TeamsDetail')
  } catch (error) {
    // 隐身小队对非成员就是 404：和不存在的小队说同一句话。
    if (error instanceof BusinessError && error.code === 404) notFound.value = true
    // 其余的留在这里画出来。原来这里 `throw`，抛出去就没人接 —— 页面一片空白，
    // 而「没读到」和「没有这个团队」在屏幕上是同一幅画面。
    else loadError.value = error
  }
}

/** 失败画面上那颗「重试」：拿地址里现在这个 handle 再问一次。 */
const retryLoad = () => fetchTeamData(String(route.params.handle))

const join = async (message: string) => {
  const {
    data: { team },
  } = await TeamsApi.join(teamData.value!.id, { message: message || undefined })
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
