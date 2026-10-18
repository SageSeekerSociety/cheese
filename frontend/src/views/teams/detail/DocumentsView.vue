<script setup lang="ts">
// 团队「文档」那一页的画面：一列文档和右上角「新建文档」。取数和跳转在同目录的
// `Documents.vue`。
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { ProjectMemberRow } from '@/cx_types'

import { ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'
import LibraryRows from '@/views/library/LibraryRows.vue'

defineProps<{
  rows: LibraryRow[]
  members: ProjectMemberRow[]
  loading: boolean
  making: boolean
  error: string
}>()

const emit = defineEmits<{
  open: [id: string]
  make: []
}>()

const listEl = ref<HTMLElement | null>(null)
</script>

<template>
  <section ref="listEl" class="team-docs">
    <div class="team-docs__bar">
      <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" :loading="making" @click="emit('make')">
        {{ t('teams.docs.new') }}
      </BaseButton>
    </div>
    <p v-if="error" role="alert" class="t-body c-danger mb-3">{{ error }}</p>
    <div v-if="loading && !rows.length" class="py-8 text-center" role="status">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>
    <LibraryRows
      v-else-if="rows.length"
      :rows="rows"
      :scroller="listEl"
      selected-path=""
      busy=""
      :browsing="true"
      :members="members"
      :agent-name="t('work.room.defaultAgentName')"
      @open-doc="emit('open', $event)"
    />
    <BaseEmptyState v-else size="inline" align="center" :title="t('teams.docs.empty')" />
  </section>
</template>

<style scoped>
.team-docs {
  padding-inline: 24px;
}
.team-docs__bar {
  display: flex;
  justify-content: flex-end;
  margin: 16px 0 12px;
}
</style>
