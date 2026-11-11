<script setup lang="ts">
// 首页's sidebar: the product's own home list (HomeNav.vue) under the 「首页」
// header, as HomeSidebarView draws it: 待办, my teams (my own one first, then
// 团队, each unfolding into its 项目 and 资料库), 新建或加入团队, and 空间, the
// spaces I belong to. The course is one of those spaces; it is not on the rail.
//
// HomeNav takes props and links by route name. The demo's memory router has
// those names, so a click lands there; this file reads where it landed and
// moves the demo (a space opens its 题目 list, a team's 项目 opens that team's
// projects), and puts the router back on the page the demo is showing.
import type { SpaceLink, Team } from '@/types'

import { computed, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'

import { SPACE, TEAMS } from './data'
import { deadlines, openHome, openSpace, S, toast } from './state'

import { t } from '@/i18n'
import { storageGet, storageSetJson } from '@/lib/browserStorage'
import HomeNav from '@/views/home/HomeNav.vue'

// The teams unfolded on first view: my own team and the course group (HomeNav keeps this per browser).
const OPEN_KEY = 'cheesex.homeNav.openTeams'
// Without storage this does nothing and HomeNav starts with every team folded.
if (storageGet(OPEN_KEY) === null) storageSetJson(OPEN_KEY, ['linxiao', 'ics-g7'])

const teams: Team[] = TEAMS.map((x, i) => ({
  id: i + 1,
  handle: x.handle,
  name: x.name,
  intro: '',
  avatarId: 0,
  personal: x.personal,
  // 林晓 set up 「ICS 第 7 组」 and owns her own team; in 视觉语言组 she is a member.
  role: x.handle === 'vl-group' ? 'MEMBER' : 'OWNER',
  owner: { id: 1 } as Team['owner'],
  admins: { total: 0, examples: [] },
  members: { total: x.members.length, examples: [] },
}))
const spaces: SpaceLink[] = [{ id: SPACE.id, name: SPACE.name }]

const awaiting = computed(
  () => S.things.filter((x) => x.status === 'wait' && x.owner === 'linxiao').length + deadlines().length
)

const router = useRouter()
const here = () =>
  S.center === 'team' ? { name: 'TeamsDetailDefault', params: { handle: S.teamView } } : { name: 'inbox' }
watch(
  () => [S.area, S.center, S.teamView],
  () => {
    if (S.area === 'home' && S.center !== 'space') void router.replace(here())
  },
  { immediate: true }
)

const ASIDE: Record<string, string> = {
  TeamsDetailLibrary: '团队的资料库：文档和文件，同项目的资料库',
  TeamsDetailSettings: '团队设置：成员、设备、模型和额度',
  UserSettingsCompute: '个人设置：你自己的设备、模型和额度',
  TeamsDetailMembers: '邀请成员：链接已复制，7 天内有效',
  TeamsDetailProfile: '团队资料：名字、头像、简介',
  HomeTeamsExplore: '新建或加入团队：起个名字，或用邀请链接加入',
  market: '市场：技能、插件、MCP、Agent 模板',
}
const stop = router.afterEach((to) => {
  const name = String(to.name ?? '')
  if (name === 'SpacesDetailTasksList') openSpace('tasks')
  else if (name === 'inbox') {
    if (S.center !== 'inbox') openHome('inbox')
  } else if (name === 'TeamsDetailDefault') {
    const handle = String(to.params.handle)
    if (S.center !== 'team' || S.teamView !== handle) openHome('team', { teamView: handle })
  } else if (ASIDE[name]) {
    toast(ASIDE[name])
    void router.replace(here())
  }
})
onBeforeUnmount(stop)
</script>

<template>
  <nav class="ot-side ot-home" data-zone="home-nav">
    <div class="sidebar-header ot-home__head">
      <span class="t-title">{{ t('navigation.home') }}</span>
    </div>
    <div class="ot-home__scroll">
      <HomeNav
        inbox
        :teams="teams"
        :spaces="spaces"
        :awaiting="awaiting"
        :current-handle="S.center === 'team' ? S.teamView : null"
        :disbanding="null"
        :transferring="null"
        :transfer-candidates="[]"
        :create-open="false"
        @update:create-open="toast('新建空间：填名字和简介，提交审核')"
        @open-disband="toast('演示里不解散团队')"
        @open-transfer="toast('演示里不转让团队')"
        @leave="(team: Team) => toast(`演示里不退出「${team.name}」`)"
      />
    </div>
  </nav>
</template>

<style scoped>
.ot-home {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--canvas);
}

.ot-home__head {
  position: static;
  background: transparent;
  backdrop-filter: none;
}

.ot-home__scroll {
  flex: 1;
  min-height: 0;
  padding: 0 8px 8px;
  overflow: auto;
}
</style>
