<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import { useNavigation } from '@/composables/useNavigation'

import { topBarBack } from '../topBarBack'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { projectFrameOf } from '@/lib/projectFrame'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const nav = useNavigation()
const { mdAndUp } = useDisplay()
const workspace = useWorkspaceStore()

/**
 * 身后有应用内来路吗？`state.back` 是 vue-router 在每次应用内跳转时记下的上一个
 * 地址；贴链接直接打开的第一页上它是 null，这时 `history.back()` 会把人踢出整个
 * 应用。
 *
 * 这只是**兜底**：上面那套层级声明得出父级时，← 交回层级（见 `target`）。
 * 读 route 只为让它随每次跳转重算：history 的 state 不是响应式的。
 */
const cameFrom = computed(() => {
  void nav?.route
  return typeof nav?.historyState?.back === 'string'
})

/**
 * 站在项目这个框的**根**上吗？根这一层没有「上一层」可声明。
 *
 * 两端的根不是同一条路由，这不是漂移：`/projects/:projectId` 在桌面上一帧都不停，
 * WorkspaceEntry 当场 `router.replace` 去项目总览；所以桌面的根是总览，手机的根才是
 * 频道列表。总览在桌面上声明着 `backTo: 'workspace-project'`，点下去只会被弹回总览
 * 自己——一颗按了没反应的 ←。
 */
const atProjectRoot = computed(() => {
  const route = nav?.route
  if (!route || projectFrameOf(route) === null) return false
  return route.name === (mdAndUp.value ? 'workspace-overview' : 'workspace-project')
})

/**
 * 项目的根上退到项目所属的小队。后端本来就在 `ProjectOut` 里返回 `team_id`。
 * 小队页只要求登录、不要求是队员，所以这个地址对任何能打开这个项目的人都点得开。
 * `team_id` 为空的历史项目没有这一层，← 就不显示。
 */
const owningTeam = computed(() => {
  const route = nav?.route
  const projectId = route ? projectFrameOf(route) : null
  if (!projectId || !atProjectRoot.value) return null
  const project = workspace.projects.find((p) => p.id === projectId)
  const handle = project?.team_handle
  if (!handle) return null
  // 项目在某人名下时，「所属团队」就是只有他自己的那一个，地址是他的用户名，也只有他
  // 本人打得开：对他是「你名下的项目」，对被邀请进来的人没有这一层。
  if (handle === project.owner_handle) {
    return handle === myHandle() ? { name: 'TeamsDetail', params: { handle }, label: t('navigation.backTo.own') } : null
  }
  return { name: 'TeamsDetail', params: { handle }, label: t('navigation.teams') }
})

/** 框内那些真的层级关系（话题 → 话题列表、私聊 → 名册）。 */
const declaredParent = computed(() => {
  const meta = nav?.route?.meta
  if (typeof meta?.backTo !== 'string') return null
  if (meta.backOnPhoneOnly && mdAndUp.value) return null
  return { name: meta.backTo, params: {}, label: '' }
})

// 根这一层**不吃** `meta.backTo`：看板在桌面上声明的父级就是它自己会被弹回来的那
// 个地址，落到它身上等于留一颗按了没反应的按钮。没有小队，诚实的答案是没有上一
// 层——那就不显示。
const target = computed(() => (atProjectRoot.value ? owningTeam.value : declaredParent.value))

const to = computed<NavTarget | null>(() => {
  const parent = target.value
  if (!parent) return null
  try {
    // `resolve` 不传第二参时用的就是当前路由，和原来显式传 `route` 是同一份
    // currentLocation；宿主没装路由就没有地址可画。
    return nav?.router.resolve({ name: parent.name, params: parent.params }).path ?? null
  } catch {
    return null
  }
})

// 说得出去处就说：读屏和长按看到的是「返回团队」而不是一句放之四海皆准的
// 「返回上一级」。
const label = computed(() =>
  target.value?.label ? t('shell.back.to', { label: target.value.label }) : t('shell.back.up')
)

// 页面接管了这一下（topBarBack.ts）：手机上话题里的非对话页签，← 先回到对话。
const override = computed(() => (mdAndUp.value ? null : topBarBack.value))
</script>

<template>
  <BaseButton
    v-if="override"
    icon="mdi-arrow-left"
    kind="ghost"
    size="lg"
    :aria-label="override.label"
    :title="override.label"
    @click="override.onBack()"
  />
  <!-- 层级在历史之前：顶栏这一颗回答的是「这一层上面是谁」，浏览器那一颗才回答
       「我刚才在哪」。声明了父级就按声明走，没声明才回退到来路（见下一条分支）。
       `:active="false"` 不是样式偏好，是修一个 bug：这颗按钮指向的是**父**地址，
       而 vue-router 的非精确匹配认为「站在子路由上时父链接是激活的」，于是
       Vuetify 一直给它盖一层 12% 的实底遮罩——一颗永远处于按下态的返回键，在
       顶栏左上角就是一个突兀的灰方块。返回是「离开这一层」，不是「你在这儿」，
       它本来就不该有激活态。 -->
  <BaseButton
    v-else-if="to"
    :to="to"
    :active="false"
    icon="mdi-arrow-left"
    kind="ghost"
    :size="mdAndUp ? 'sm' : 'lg'"
    :aria-label="label"
    :title="label"
  />
  <!-- 这一页没声明上一层（首页、反馈中心、各设置页签……），但身后确实有应用内来路，
       按浏览器的语义退一格，好过什么都不画。 -->
  <BaseButton
    v-else-if="cameFrom"
    icon="mdi-arrow-left"
    kind="ghost"
    :size="mdAndUp ? 'sm' : 'lg'"
    :aria-label="t('shell.back.previous')"
    :title="t('shell.back.previous')"
    @click="nav?.router.back()"
  />
</template>
