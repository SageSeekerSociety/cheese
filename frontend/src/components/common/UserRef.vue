<script setup lang="ts">
// 句子里提到的一个人：「已连接（由 @某人 授权）」「@某人 采纳」。
//
// 和对话消息里的 @chip 是同一颗：同一个全局 `.mention` 样式、同一个去处
// （lib/userRef）。消息里那颗是 renderMessage 拼的 HTML，点击由对话栏委派给
// 父组件跳转；这里没有外层可以委派，点了就只说一句「去了」。
//
// **画的是身份，不只是名字**：头像 + @名字，都交给 `IdentityView`（人/队友的分支、
// 缺头像的降级只此一处）。身份从哪来？handle 和名字走 props，头像素材 id 和「是不是
// 队友」也走 props —— 名册查询、当前项目、路由跳转在 composables/useUserRef 里
// （components/common/UserRefLink.vue 那只薄容器把它接上）。这样它在没装 store、
// 没装路由的树里也能单独渲染——/demo 里的卡片、单测都不必为了画一个人先搭一整套
// 环境：没有那些 props 时画 handle + 彩色首字母，仍是同一个人。
//
// 没有 handle（只知道名字）时照样画 @名字，但不可点——没有 handle 就找不到那个人的
// 主页。所以这里要几组 prop：handle 决定能不能去、名字画成什么、头像画什么。
import type { UserRefTarget } from '@/lib/userRef'

import { computed } from 'vue'

import IdentityView from './IdentityView.vue'

const props = withDefaults(
  defineProps<{
    handle?: string | null
    /** 显示名。不传就画 handle。 */
    name?: string | null
    /** 去处。为空表示没有去处：照样画 @名字，但不可点。 */
    to?: UserRefTarget | null
    /**
     * 这个人自己挑过的头像素材 id（名册/接口给的）。不传＝没有头像，画彩色首字母。
     * 只有不经过 `useUserRef` 的直接使用者才需要自己传；`UserRefLink` 会从名册接上。
     */
    avatarId?: number | null
    /**
     * 是不是 AI 队友，照名册上说。三态：true/false 是名册说了算，不传（null）交给
     * `IdentityView` 按 handle 命名规则兜底——Vue 会把没传的布尔 prop 钉成 false，
     * 所以这里用 `boolean | null` 加 `null` 默认，兜底才轮得到。
     */
    agent?: boolean | null
  }>(),
  { handle: null, name: null, to: null, avatarId: null, agent: null }
)

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
    class="mention user-ref"
    :data-handle="handle"
    data-user-content
    role="link"
    tabindex="0"
    @click="go"
    @keydown.enter.prevent="go"
  >
    <IdentityView :handle="handle" :name="name" :avatar-id="avatarId" :agent="agent" :size="16" mention />
  </span>
  <span v-else-if="label" class="mention mention--static user-ref" data-user-content>
    <IdentityView :handle="handle" :name="name" :avatar-id="avatarId" :agent="agent" :size="16" mention />
  </span>
</template>

<style scoped>
/* 一颗 chip 里现在坐着头像 + @名字，是一段行内 flex；`middle` 让它和周围文字对齐。 */
.user-ref {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  max-width: 100%;
}
</style>
