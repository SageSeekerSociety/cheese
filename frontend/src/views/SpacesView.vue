<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listSpaces } from '../api'
import type { Space } from '../cx_types'

const spaces = ref<Space[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const payload = await listSpaces()
    spaces.value = payload.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载机构失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="spaces-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 1100px">
      <div class="mb-6">
        <div class="t-eyebrow mb-1">机构看板</div>
        <h1 class="t-page-title">机构</h1>
      </div>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable">
        {{ error }}
      </v-alert>
      <div v-else-if="spaces.length === 0" class="empty-state">
        <v-icon size="34" class="empty-state__icon">mdi-office-building-outline</v-icon>
        <div class="t-body c-muted">暂无机构</div>
      </div>

      <v-row v-else>
        <v-col v-for="s in spaces" :key="s.id" cols="12" sm="6" md="4">
          <v-card
            :to="{ name: 'space-board', params: { spaceId: s.id } }"
            hover
            height="100%"
          >
            <v-card-item>
              <template #prepend>
                <div class="space-avatar">
                  <v-icon size="20">mdi-office-building-outline</v-icon>
                </div>
              </template>
              <v-card-title class="t-title">{{ s.name }}</v-card-title>
            </v-card-item>
            <v-card-text class="d-flex align-center c-muted t-body">
              查看看板
              <v-icon end size="15">mdi-arrow-right</v-icon>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </v-container>
  </div>
</template>

<style scoped>
.spaces-page {
  background: var(--canvas);
}
/* Neutral institution avatar — rounded-square, ink glyph on fill. */
.space-avatar {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: var(--fill);
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 56px 0;
}
.empty-state__icon {
  color: var(--line-2);
}
</style>
