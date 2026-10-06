<template>
  <!-- 没加入的人看到小队的对外一面，不进成员工作区，也不去拉只有成员读得到的东西。 -->
  <v-container v-if="teamData && !isMember" class="fill-height justify-center pa-4" fluid>
    <TeamProfile :team="teamData" :join="join" />
  </v-container>
  <v-container v-else-if="notFound" class="fill-height justify-center pa-4" fluid>
    <p class="t-body c-muted">{{ t('work.teamProfile.notFound') }}</p>
  </v-container>
  <!-- 读失败原来直接抛出去，这一页就什么都不画 —— 整页空白，看不出是没有这个团队还是没读到。 -->
  <v-container v-else-if="loadError" class="fill-height justify-center pa-4" fluid>
    <BaseLoadError
      :title="t('work.teamProfile.loadFailed')"
      :error="loadFailureReason(loadError)"
      :forbidden="isForbidden(loadError)"
      @retry="retryLoad"
    />
  </v-container>
  <AppPage
    v-else-if="teamData"
    :title="pageTitle"
    :parent="{ label: teamData.name, to: { name: 'TeamsDetailDefault', params: { handle: teamData.handle } } }"
    :width="pageWidth"
  >
    <template v-if="teamIntro" #meta>
      <span class="team-intro" data-user-content>{{ teamIntro }}</span>
    </template>
    <router-view />
  </AppPage>
</template>

<script setup lang="ts">
// 团队详情外框：页头（团队名 / 这一页）+ 当前这一页。团队的几样东西（项目、成员、
// 知识库、工作电脑、额度）列在首页侧栏里这个团队的下面，这里不再自己画一条侧栏。
import type { Team } from '@/types'

import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import TeamProfile from './TeamProfile.vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'
import { usePageTitleStore } from '@/stores/title'

const route = useRoute()
const titles = usePageTitleStore()
const teamData = ref<Team>()
provide(teamDataInjectionKey, teamData)

const notFound = ref(false)
/** 这一次没读到的那个错。404 是「没有这个团队」，另走一条；其余是非空就画失败。 */
const loadError = ref<unknown>(null)
const isMember = computed(() => teamData.value?.joinStatus === 'member')

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
const pageWidth = computed(() => (READ_PAGES.has(String(route.name)) ? 'read' : 'full'))

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

<style scoped>
.team-intro {
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
