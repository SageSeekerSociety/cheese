<script setup lang="ts">
import { computed } from 'vue'

import AdminFeaturePageView from './AdminFeaturePageView.vue'

import { findFeatureView } from '@/views/admin/features/registry'

// 功能数据页的**入口壳**（`/admin/feature-stats/:id`）。
//
// 为什么是一条带参数的**一条路由**，而不是每个功能各写一条：地址里的 id 和服务端注册表
// 里的 `FEATURE_ID` 本来就一一对应，一条路由只是把这件事直说。加一个功能时不需要动
// `router/feedback.ts`（少一处会忘、忘了只在点进去那一刻才发现的地方），页面的形状由
// 前端注册表里那个组件自己定 —— 每个功能的数据长得不一样，硬塞进同一个模板里迟早
// 会被撑破。
//
// 认不出的 id 画一句「还没有这一页」，不重定向、不 404：地址是从外面粘进来的，静悄悄
// 换掉它等于否认用户看到过的那个 URL（同 `router/feedback.ts` 里那条「留着老地址」的
// 理由）。这一页自己不发请求 —— 请求是各个功能页的事，这里的职责只有「按 id 挑组件」。
//
// 画的那一半在 `AdminFeaturePageView.vue`：这里只留下地址和注册表，画面只收 props。
defineOptions({ name: 'AdminFeaturePage' })

// 地址里那一段由路由记录以 prop 送进来（`router/feedback.ts` 上那行 `props: true`）。
const props = defineProps<{ id: string }>()

const feature = computed(() => findFeatureView(String(props.id)))
</script>

<template>
  <AdminFeaturePageView :id="props.id" :feature="feature" />
</template>
