<template>
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

<script setup lang="ts">
// 小队详情外框：侧栏（项目/成员/知识库/算力）+ 当前 tab。原来的聊天频道已随
// "都归项目" 的决定退役 —— 会话面是项目里的话题，这里只剩小队的资产。
import type { Team, User } from '@/types'

import { computed, onMounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'

import DetailSidebar from './DetailSidebar.vue'

import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'

const route = useRoute()
const teamData = ref<Team>()
provide(teamDataInjectionKey, teamData)

const teamMembers = ref<User[]>([])
const teamMembersCount = ref(0)

const headerTitle = computed(() =>
  route.name === 'TeamsDetailMembers' ? '成员管理' : route.name === 'TeamsDetailKnowledge' ? '知识库' : null
)
const headerIcon = computed(() =>
  route.name === 'TeamsDetailMembers' ? 'mdi-account-group' : 'mdi-book-open-page-variant'
)

const fetchTeamData = async (teamId: number) => {
  const {
    data: { team },
  } = await TeamsApi.detail(teamId)
  teamData.value = team
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

onMounted(async () => {
  const teamId = Number(route.params.teamId)
  await Promise.all([fetchTeamData(teamId), fetchTeamMembers(teamId)])
})
</script>

<style scoped lang="scss">
.layout-container {
  height: calc(100vh - var(--v-layout-top) - 1px);
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
    height: calc(100vh - 56px);
  }
}
</style>
