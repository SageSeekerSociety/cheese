<script setup lang="ts">
// 空间外壳「画」的那一半：给每一页套一层渲染兜底。
//
// 只认 props：换页的键（resetKey）与上报（onError）都由容器传进来，这一半不读路由、
// 不引 errorReporter，因此能在没装路由的树里渲染。
//
// 为什么要有这层兜底：某页在渲染里抛错时，Vue 会把它整棵子树换成空注释、那一块直接
// 白掉（「待审核」曾经就是这样：模块求值时抛错，整页连同侧栏一起没了）。
// ErrorBoundaryView 把这一块换成一句提示加一颗「重试」，别的部分照旧；resetKey 用当前
// 路由，换一页或换一个空间时那层自己活过来。
import ErrorBoundaryView from '@/components/common/ErrorBoundaryView.vue'

defineProps<{
  /** 当前路由。换一页或换一个空间时那层兜底自己活过来。 */
  resetKey: string
  /** 子树里第一次没人接的错：上报一次（容器传 reportError）。 */
  onError: (message: string, stack: string | undefined, source: string) => void
}>()
</script>

<template>
  <ErrorBoundaryView :reset-key="resetKey" :on-error="onError">
    <RouterView />
  </ErrorBoundaryView>
</template>
