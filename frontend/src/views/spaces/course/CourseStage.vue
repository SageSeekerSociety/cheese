<template>
  <!--
    一个课程格的骨架：标题 + 一句「这一屏会是什么」。

    它存在是为了**先把入口挂上**：路由与侧栏那几格这一条任务一次加齐，后面的任务
    只把占位换成真页面，不再动路由。所以这里不画假数据、不假装配好了 —— 一句
    说清这一屏将来是什么，比一张看起来像真的空表诚实。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-2">{{ t(titleKey) }}</h1>
    <p class="text-medium-emphasis mb-0">{{ t(bodyKey) }}</p>
  </v-sheet>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  titleKey: string
  bodyKey: string
}>()
</script>
