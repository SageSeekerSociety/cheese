<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { projectFrameOf, readEntry } from '@/lib/projectEntry'
import { useWorkspaceStore } from '@/stores/workspace'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const workspace = useWorkspaceStore()

/**
 * 站在项目这个框的**根**上吗？根这一层没有「上一层」可声明，← 得靠记下来的来路。
 *
 * 两端的根不是同一条路由，这不是漂移：`/projects/:projectId` 在桌面上一帧都不停，
 * WorkspaceEntry 当场 `router.replace` 去看板；所以桌面的根是看板，手机的根才是
 * 话题列表。看板在桌面上原本声明着 `backTo: 'workspace-project'`，点下去只会被弹
 * 回看板自己——一颗按了没反应的 ←。
 */
const atProjectRoot = computed(() => {
  if (projectFrameOf(route) === null) return false
  return route.name === (mdAndUp.value ? 'workspace-running' : 'workspace-project')
})

/** 记下来的来路：从项目外面走进来的那一跳。 */
const entry = computed(() => {
  const projectId = projectFrameOf(route)
  if (!projectId || !atProjectRoot.value) return null
  const saved = readEntry(projectId)
  if (!saved) return null
  // 存的是路由名 + 参数，不是地址：小队被删、路由改名之后 resolve 会抛，此时落回
  // 兜底，而不是把人送进一个 404。
  try {
    router.resolve({ name: saved.name, params: saved.params })
  } catch {
    return null
  }
  return saved
})

/**
 * 兜底：项目所属的小队。后端本来就在 `ProjectOut` 里返回 `team_id`，不用改。
 * 小队页只要求登录、不要求是队员，所以这个地址对任何能打开这个项目的人都点得开。
 * `team_id` 为空的历史项目没有这一层，← 就不显示。
 */
const owningTeam = computed(() => {
  const projectId = projectFrameOf(route)
  if (!projectId || !atProjectRoot.value || entry.value) return null
  const teamId = workspace.projects.find((p) => p.id === projectId)?.team_id
  return typeof teamId === 'number' ? { name: 'TeamsDetail', params: { teamId: String(teamId) }, label: '小队' } : null
})

/** 框内那些真的层级关系（话题 → 话题列表、私聊 → 名册）——那些本来就是对的。 */
const declaredParent = computed(() =>
  typeof route.meta.backTo === 'string' ? { name: route.meta.backTo, params: {}, label: '' } : null
)

// 根这一层**不吃** `meta.backTo`：看板在桌面上声明的父级就是它自己会被弹回来的那
// 个地址，落到它身上等于留一颗按了没反应的按钮。没有来路也没有小队，诚实的答案
// 是没有上一层——那就不显示。
const target = computed(() => (atProjectRoot.value ? entry.value ?? owningTeam.value : declaredParent.value))

const to = computed<RouteLocationRaw | null>(() => {
  const t = target.value
  if (!t) return null
  try {
    return router.resolve({ name: t.name, params: t.params }, route).path
  } catch {
    return null
  }
})

// 说得出去处就说：读屏和长按看到的是「返回小队」而不是一句放之四海皆准的
// 「返回上一级」。名字是**离开那一页时**存下来的——现在再去取，那一页早卸载了。
const label = computed(() => (target.value?.label ? `返回${target.value.label}` : '返回上一级'))
</script>

<template>
  <!-- `:active="false"` 不是样式偏好，是修一个 bug：这颗按钮指向的是**父**地址，
       而 vue-router 的非精确匹配认为「站在子路由上时父链接是激活的」，于是
       Vuetify 一直给它盖一层 12% 的实底遮罩——一颗永远处于按下态的返回键，在
       顶栏左上角就是一个突兀的灰方块。返回是「离开这一层」，不是「你在这儿」，
       它本来就不该有激活态。 -->
  <v-btn
    v-if="to"
    :to="to"
    :active="false"
    icon
    color="on-surface-variant"
    variant="text"
    :size="mdAndUp ? 28 : 44"
    :aria-label="label"
    :title="label"
  >
    <v-icon size="20">mdi-arrow-left</v-icon>
  </v-btn>
</template>
