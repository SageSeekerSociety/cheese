<script setup lang="ts">
// 句子里提到的一个人：「已连接（由 @某人 授权）」「@某人 采纳」。
//
// 和对话消息里的 @chip 是同一颗：同一个全局 `.mention` 样式、同一个去处
// （lib/userRef）。消息里那颗是 renderMessage 拼的 HTML，点击由对话栏委派给
// 父组件跳转；这里没有外层可以委派，自己跳。
//
// 只给 handle 不给名字时，名字从项目名册里找（和对话栏给 <@handle> 取名字是同一份名册），
// 找不到就写 handle。没有 handle（只知道名字）时照样画 @名字，但不可点——没有 handle
// 就找不到那个人的主页。
import { computed, getCurrentInstance } from 'vue'

import { userRefRoute } from '@/lib/userRef'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  handle?: string | null
  name?: string | null
  /** 不传时取当前路由上的 projectId：在项目里就去项目里的成员页。传 null 表示这句话
   *  不属于眼下这个项目（例如全局通知），去个人主页。 */
  projectId?: string | null
}>()

// 路由和 pinia 都从 app 上拿，而不是 useRouter()/useRoute()：这颗 chip 散落在各处，
// 有的所在树没装路由（孤立渲染的卡片），那里照样画 @名字，只是没有去处。
const app = getCurrentInstance()?.appContext.config.globalProperties

// 名册在项目框里才有，调用方给了名字就不用它。
const store = app?.$pinia ? useWorkspaceStore() : null
const label = computed(() => {
  if (props.name) return props.name
  const row = props.handle ? store?.members.find((m) => m.user_handle === props.handle) : undefined
  return row?.name || props.handle || ''
})
const target = computed(() => {
  if (!props.handle || !app?.$router) return null
  const pid = props.projectId !== undefined ? props.projectId : (app.$route?.params?.projectId as string | undefined)
  return userRefRoute(props.handle, pid)
})

function go(e: Event) {
  if (!target.value) return
  // 所在的行、卡片自己往往也可点（展开、打开详情）；点人名只该去这个人那里。
  e.stopPropagation()
  void app?.$router.push(target.value)
}
</script>

<template>
  <span
    v-if="target"
    class="mention"
    :data-handle="handle"
    role="link"
    tabindex="0"
    @click="go"
    @keydown.enter.prevent="go"
    >@{{ label }}</span
  >
  <span v-else-if="label" class="mention mention--static">@{{ label }}</span>
</template>
