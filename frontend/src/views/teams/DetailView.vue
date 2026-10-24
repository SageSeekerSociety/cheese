<!--
  The rendering half of Detail.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the request and the tab title.
-->

<script setup lang="ts">
import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'
import type { NavTarget } from '@/lib/navTarget'
import type { Team } from '@/types'

import { getAvatarUrl } from '@/utils/materials'

import TeamProfile from './TeamProfile.vue'
import TeamSettingsView from './TeamSettingsView.vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'

defineProps<{
  teamData?: Team
  /** 读回来了，而且这个人是成员：画工作区那一面，不是小队对外那一面。 */
  isMember: boolean
  /** 这个地址上没有这个小队（隐身小队对非成员也是这一种）。 */
  notFound: boolean
  /** 这一次没读到（404 不算，那走 notFound）。 */
  failed: boolean
  /** 失败那一格上的那句话；容器判好的。 */
  failureReason: string | null
  /** 「不给你看」，和「这次没读到」分开画。 */
  forbidden: boolean
  /** 这一页的标题：团队名 / 这一栏。 */
  pageTitle: string
  /** 读的一栏跟着正文封顶居中，其余几页铺满内容区。 */
  pageWidth: 'read' | 'full'
  teamIntro: string
  /** 没加入的人点「加入」走哪条路，由容器接上。 */
  join: (message: string) => Promise<void>
  /** 停在设置的某一栏上：这一页画成盖在窗口上的「团队设置」那一层，不画团队页的外框。 */
  settings?: { groups: SettingsGroup[]; active: string | null; backTo: NavTarget } | null
}>()

const emit = defineEmits<{ 'retry-load': []; 'close-settings': [] }>()

/** 当前这一栏的正文由容器给：它在那儿挂 `<router-view>`。 */
defineSlots<{ default?: () => unknown }>()
</script>

<template>
  <!-- 没加入的人看到小队的对外一面，不进成员工作区，也不去拉只有成员读得到的东西。 -->
  <v-container v-if="teamData && !isMember" class="fill-height justify-center pa-4" fluid>
    <TeamProfile :team="teamData" :join="join" />
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
      @retry="emit('retry-load')"
    />
  </v-container>
  <TeamSettingsView
    v-else-if="teamData && settings"
    :name="teamData.name"
    :handle="teamData.handle"
    :avatar="getAvatarUrl(teamData.avatarId)"
    :groups="settings.groups"
    :active="settings.active"
    :back-to="settings.backTo"
    @close="emit('close-settings')"
  >
    <slot />
  </TeamSettingsView>
  <AppPage
    v-else-if="teamData"
    :title="pageTitle"
    :parent="{ label: teamData.name, to: { name: 'TeamsDetailDefault', params: { handle: teamData.handle } } }"
    :width="pageWidth"
  >
    <template v-if="teamIntro" #meta>
      <span class="team-intro" data-user-content>{{ teamIntro }}</span>
    </template>
    <slot />
  </AppPage>
</template>

<style scoped>
.team-intro {
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
