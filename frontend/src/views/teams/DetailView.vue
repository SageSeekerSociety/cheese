<template>
  <!-- 没加入的人看到小队的对外一面，不进成员工作区，也不去拉只有成员读得到的东西。 -->
  <v-container v-if="team && !isMember" class="fill-height justify-center pa-4" fluid>
    <TeamProfile :team="team" :join="join" :resolve-user="resolveUser" @navigate="$emit('navigate', $event)" />
  </v-container>
  <v-container v-else-if="notFound" class="fill-height justify-center pa-4" fluid>
    <p class="t-body c-muted">{{ t('work.teamProfile.notFound') }}</p>
  </v-container>
  <!-- 读失败原来直接抛出去，这一页就什么都不画 —— 整页空白，看不出是没有这个团队还是没读到。 -->
  <v-container v-else-if="failed" class="fill-height justify-center pa-4" fluid>
    <BaseLoadError
      :title="t('work.teamProfile.loadFailed')"
      :error="failureReason"
      :forbidden="forbidden"
      @retry="$emit('retry')"
    />
  </v-container>
  <AppPage
    v-else-if="team"
    :title="pageTitle"
    :parent="{ label: team.name, to: { name: 'TeamsDetailDefault', params: { handle: team.handle } } }"
    :width="pageWidth"
  >
    <template v-if="teamIntro" #meta>
      <span class="team-intro" data-user-content>{{ teamIntro }}</span>
    </template>
    <router-view />
  </AppPage>
</template>

<script setup lang="ts">
// 团队详情**画的那一半**：小队的外框（页头 + 当前这一页），没加入时是小队的对外一面。
//
// 取数、读地址、读标题 store、人名与去处都在容器 `Detail.vue` 里；这里只吃 props、
// 只往上发事件。团队的几样东西（项目、成员、知识库、工作电脑、额度）列在首页侧栏里
// 这个团队的下面，这一页不再自己画一条侧栏。
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { Team } from '@/types'

import TeamProfile from './TeamProfile.vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'

defineProps<{
  team: Team | undefined
  isMember: boolean
  /** 地址里的小队不存在：隐身小队对非成员就是同一个答案。 */
  notFound: boolean
  /** 这一次没读到 —— `team` 空不是「没有这个团队」，是「没读到」。 */
  failed: boolean
  failureReason: string | null
  /** 401/403：不是「没读到」，是「不给你看」，失败那一格不画重试。 */
  forbidden: boolean
  pageTitle: string
  pageWidth: 'read' | 'full'
  teamIntro: string
  /** 「加入」走哪条接口由容器给：按地址打开和按小队链接打开不是同一条。 */
  join: (message: string) => Promise<void>
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

defineEmits<{
  retry: []
  navigate: [target: ResolvedUserRef['to']]
}>()
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
