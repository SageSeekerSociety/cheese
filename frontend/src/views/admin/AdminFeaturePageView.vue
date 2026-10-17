<!--
  The rendering half of AdminFeaturePage.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the address and the feature registry.
-->

<script setup lang="ts">
import type { FeatureView } from '@/views/admin/features/registry'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

defineProps<{
  /** 注册表里认出的那一个功能；地址里那段 id 认不出时是 `undefined`。 */
  feature: FeatureView | undefined
  /** 地址里那一段。认不出 id 时要用它说出是哪一个。 */
  id: string
}>()
</script>

<template>
  <component :is="feature.view" v-if="feature" />

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
