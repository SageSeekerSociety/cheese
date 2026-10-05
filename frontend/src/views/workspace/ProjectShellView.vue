<script setup lang="ts">
// 项目工作台的框架层，画的这一半：一句给读屏器听的项目名标题、打不开时的说明块、
// 以及承载子路由的出口。取项目、跟着地址记当前话题、轮询角标、报错弹 toast 都在
// 容器 ProjectShell.vue 里 —— 这一半只收 props，只把「取消归档」抛上去。
//
// 出口 `<router-view>` 直接写在这里而不是容器里：容器的那张图里不能出现
// `<component :is>`（见 .claude/scripts/scene-ratchet.py 的 paired_views），
// 而在这一半里它只是「一话题一个实例」的那句话本身，不接路由、不取数。
import ProjectAccessNotice from '@/views/workspace/ProjectAccessNotice.vue'

defineProps<{
  projectName: string | null
  accessDenied: 'unauthenticated' | 'forbidden' | 'archived' | null
  isOwner: boolean
  accessError: string | null
  restoring: boolean
}>()

defineEmits<{ restore: [] }>()
</script>

<template>
  <div class="project-shell fill-height">
    <!-- Screen-reader heading for the frame. Text from the same store the top
         bar reads (the `project-frame` dynamic title), so the two can never
         drift. Hidden: on desktop the project name lives in the sidebar's top
         row, on mobile in the top bar; neither is a heading. -->
    <h1 v-if="projectName" class="visually-hidden">{{ projectName }}</h1>
    <!-- When the project cannot be opened the whole content area becomes an
         explanation rather than a shell to guess at. The sidebar and top bar
         stay, because the ways out of here live on them. -->
    <ProjectAccessNotice
      v-if="accessDenied"
      :reason="accessDenied"
      :is-owner="isOwner"
      :error="accessError"
      :restoring="restoring"
      @restore="$emit('restore')"
    />
    <!-- One instance per topic: changing topic swaps the whole component tree.
         Reusing one instance would leave every component under a topic to
         remember to clear itself and drop the previous topic's late responses —
         miss one spot and the old topic shows through in the new one. Only the
         topic page carries a topicId; other pages are unaffected. -->
    <router-view v-else v-slot="{ Component, route: current }">
      <component :is="Component" :key="current.params.topicId" />
    </router-view>
  </div>
</template>

<style scoped>
.project-shell {
  width: 100%;
  overflow: hidden;
}
</style>
