<template>
  <!-- 没加入的人看到小队的对外一面，不进成员工作区，也不去拉只有成员读得到的东西。 -->
  <v-container v-if="teamData && !isMember" class="fill-height justify-center pa-4" fluid>
    <TeamProfile :team="teamData" :join="join" />
  </v-container>
  <v-container v-else-if="notFound" class="fill-height justify-center pa-4" fluid>
    <p class="t-body c-muted">{{ t('work.teamProfile.notFound') }}</p>
  </v-container>
  <template v-else-if="teamData">
    <DetailSidebar :team-data="teamData" :team-members-count="teamMembersCount" />
    <v-container fluid class="pa-0 layout-container">
      <v-row no-gutters class="fill-height">
        <!-- 右侧内容区 -->
        <v-col>
          <v-sheet class="h-100 d-flex flex-column" rounded="lg">
            <!-- 成员/知识库沿用公共头；项目、算力 tab 自带标题行。 -->
            <div v-if="headerTitle" class="content-header px-6 py-3 d-flex align-center">
              <v-icon :icon="headerIcon" class="mr-2"></v-icon>
              <h2 class="text-h6 font-weight-medium">{{ headerTitle }}</h2>
            </div>
            <v-divider v-if="headerTitle"></v-divider>

            <div class="content-body overflow-auto">
              <router-view />
            </div>
          </v-sheet>
        </v-col>
      </v-row>
    </v-container>
  </template>
</template>

<script setup lang="ts">
// 小队详情外框：侧栏（项目/成员/知识库/算力）+ 当前 tab。原来的聊天频道已随
// "都归项目" 的决定退役 —— 会话面是项目里的话题，这里只剩小队的资产。
import type { Team, User } from '@/types'

import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import DetailSidebar from './DetailSidebar.vue'
import TeamProfile from './TeamProfile.vue'

import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const route = useRoute()
const teamData = ref<Team>()
provide(teamDataInjectionKey, teamData)

const teamMembers = ref<User[]>([])
const teamMembersCount = ref(0)
const notFound = ref(false)
const isMember = computed(() => teamData.value?.joinStatus === 'member')

const headerTitle = computed(() =>
  route.name === 'TeamsDetailMembers' ? '成员管理' : route.name === 'TeamsDetailKnowledge' ? '知识库' : null
)
const headerIcon = computed(() =>
  route.name === 'TeamsDetailMembers' ? 'mdi-account-group' : 'mdi-book-open-page-variant'
)

const fetchTeamData = async (teamId: number) => {
  notFound.value = false
  try {
    const {
      data: { team },
    } = await TeamsApi.detail(teamId)
    teamData.value = team
  } catch (error) {
    // 隐身小队对非成员就是 404：和不存在的小队说同一句话。
    if (error instanceof BusinessError && error.code === 404) notFound.value = true
    else throw error
  }
}

const fetchTeamMembers = async (teamId: number) => {
  try {
    const response = await TeamsApi.getMembers(teamId)
    teamMembers.value = response.data.members.map((member) => member.user)
    teamMembersCount.value = response.data.members.length
  } catch (error) {
    console.error('获取小队成员失败', error)
  }
}

const join = async (message: string) => {
  const {
    data: { team },
  } = await TeamsApi.join(teamData.value!.id, { message: message || undefined })
  teamData.value = team
}

watch(
  () => route.params.teamId,
  async (id) => {
    teamData.value = undefined
    await fetchTeamData(Number(id))
  },
  { immediate: true }
)

// 成员名单只有成员读得到：成了成员（包括刚刚直接加入）才去拉。
watch(isMember, (member) => {
  if (member && teamData.value) void fetchTeamMembers(teamData.value.id)
})
</script>

<style scoped lang="scss">
.layout-container {
  height: calc(100dvh - var(--v-layout-top) - 1px);
  overflow: hidden;
}

.content-header {
  min-height: 60px;
}

.content-body {
  flex: 1;
  overflow-y: auto;
}

@media (max-width: 960px) {
  .layout-container {
    height: auto;
    overflow: visible;
  }
}

@media (max-width: 600px) {
  .layout-container {
    height: calc(100dvh - 56px);
  }
}
</style>
