<script setup lang="ts">
// 功能数据页**入口壳的画面**：按 id 挑出来的那个功能组件，或认不出 id 时那一句
// 「还没有这一页」。
//
// 「按 id 挑组件」是容器 `AdminFeaturePage.vue` 的活（它读地址、查注册表），这里只收
// 结果 —— 一个组件或 `null`。所以这一只不碰路由、不发请求，能单独挂起来看。
import type { Component } from 'vue'

import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

defineProps<{
  /** 注册表里认出来的那个功能页；认不出就是 `null`。 */
  feature: Component | null
  /** 地址里那一段 id，认不出时写进那句说明里。 */
  id: string
}>()

const { t } = useI18n()
</script>

<template>
  <component :is="feature" v-if="feature" />

  <!-- 认不出的 id：页头写这一块的名字（和侧栏那一项一致），正文一句「还没有这一页」。 -->
  <AdminPage v-else :title="t('navigation.admin.featureStats')">
    <div class="admin-page__body">
      <BaseEmptyState
        :title="t('featureStats.page.unknown')"
        :desc="t('featureStats.page.unknownDesc', { id })"
        tone="error"
      />
    </div>
  </AdminPage>
</template>
