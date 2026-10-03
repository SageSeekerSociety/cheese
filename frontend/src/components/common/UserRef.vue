<script setup lang="ts">
// 句子里提到的一个人：「已连接（由 @某人 授权）」「@某人 采纳」。
//
// 和对话消息里的 @chip 是同一颗：同一个全局 `.mention` 样式、同一个去处
// （lib/userRef）。消息里那颗是 renderMessage 拼的 HTML，点击由对话栏委派给
// 父组件跳转；这里没有外层可以委派，点了就只说一句「去了」。
//
// **只认 props**：handle 和名字画成 @名字，给了 to 才可点。名册查询、当前项目、
// 路由跳转都在 composables/useUserRef 里（components/common/UserRefLink.vue 那只
// 薄容器把它接上）。这样它在没装 store、没装路由的树里也能单独渲染——/demo 里的
// 卡片、单测都不必为了画一个人名先搭一整套环境。
//
// 没有 handle（只知道名字）时照样画 @名字，但不可点——没有 handle 就找不到那个人的
// 主页。所以这里要两个 prop：handle 决定能不能去，name 决定画成什么。
import type { UserRefTarget } from '@/lib/userRef'

import { computed } from 'vue'

const props = defineProps<{
  handle?: string | null
  /** 显示名。不传就画 handle。 */
  name?: string | null
  /** 去处。为空表示没有去处：照样画 @名字，但不可点。 */
  to?: UserRefTarget | null
}>()

const emit = defineEmits<{ navigate: [] }>()

const label = computed(() => props.name || props.handle || '')

function go(e: Event) {
  if (!props.to) return
  // 所在的行、卡片自己往往也可点（展开、打开详情）；点人名只该去这个人那里。
  e.stopPropagation()
  emit('navigate')
}
</script>

<template>
  <span
    v-if="to"
    class="mention"
    :data-handle="handle"
    data-user-content
    role="link"
    tabindex="0"
    @click="go"
    @keydown.enter.prevent="go"
    >@{{ label }}</span
  >
  <span v-else-if="label" class="mention mention--static" data-user-content>@{{ label }}</span>
</template>
