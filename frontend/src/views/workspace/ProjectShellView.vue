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
    <!-- 进不来的时候，整块内容区换成说明，而不是让人对着一个空壳猜。侧栏和顶栏
         留着，因为「离开这里」的路都在那上面。 -->
    <ProjectAccessNotice
      v-if="accessDenied"
      :reason="accessDenied"
      :is-owner="isOwner"
      :error="accessError"
      :restoring="restoring"
      @restore="$emit('restore')"
    />
    <!-- 一个话题一个实例：换话题就换一整棵组件树。复用同一个实例的话，每个挂在话题
         下面的组件都得自己记得在换话题时清空、并丢掉上一个话题迟到的响应——漏一处，
         上一个话题的东西就会在下一个话题里露出来。只有话题页带 topicId，别的页
         不受影响。 -->
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
