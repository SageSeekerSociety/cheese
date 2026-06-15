<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { getSpaceDashboard } from '../api'
import { AI_MODE, label } from '../labels'
import type { SpaceDashboard, SpaceTeam } from '../types'

const props = defineProps<{ spaceId: string }>()

const dashboard = ref<SpaceDashboard | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

function fmtDate(d: string | null): string {
  if (!d) return '待定'
  return d.length >= 10 ? d.slice(0, 10) : d
}

// Active count = anything not archived/draft. Used for the one-glance signal.
function activeCount(team: SpaceTeam): number {
  const byStatus = team.topics_by_status ?? {}
  return byStatus.active ?? 0
}

// A team is "stale" when it has topics but none active (nothing moving).
function isStale(team: SpaceTeam): boolean {
  return team.topic_count > 0 && activeCount(team) === 0
}

const headers = [
  { title: '团队', key: 'name' },
  { title: '负责人', key: 'owner_handle' },
  { title: 'AI 模式', key: 'ai_mode' },
  { title: '话题数', key: 'topic_count', align: 'end' as const },
  { title: '活跃', key: 'active', align: 'end' as const },
  { title: '下个里程碑', key: 'next_milestone' },
  { title: '状态', key: 'state' },
  { title: '', key: 'data-table-expand' },
]

const teams = computed<SpaceTeam[]>(() => dashboard.value?.teams ?? [])

async function load() {
  loading.value = true
  error.value = null
  try {
    dashboard.value = await getSpaceDashboard(props.spaceId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载看板失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.spaceId, load)
onMounted(load)
</script>

<template>
  <div class="board-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 1200px">
      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable">
        {{ error }}
      </v-alert>

      <template v-else-if="dashboard">
        <div class="mb-6">
          <v-btn
            :to="{ name: 'spaces' }"
            variant="text"
            size="small"
            prepend-icon="mdi-arrow-left"
            class="mb-2 px-1"
          >
            机构列表
          </v-btn>
          <div class="t-eyebrow mb-1">机构看板</div>
          <h1 class="t-page-title">{{ dashboard.name }}</h1>
        </div>

        <v-card class="board-table">
          <v-data-table
            :headers="headers"
            :items="teams"
            item-value="project_id"
            density="compact"
            :items-per-page="-1"
            hide-default-footer
            show-expand
            hover
            no-data-text="该机构暂无团队"
          >
            <template #item.name="{ item }">
              <span class="board-name">{{ item.name }}</span>
            </template>
            <template #item.owner_handle="{ item }">
              <span class="c-muted">@{{ item.owner_handle }}</span>
            </template>
            <template #item.ai_mode="{ item }">
              <span class="board-tag">{{ label(AI_MODE, item.ai_mode) }}</span>
            </template>
            <template #item.topic_count="{ item }">
              <span class="board-num">{{ item.topic_count }}</span>
            </template>
            <template #item.active="{ item }">
              <span class="board-num">{{ activeCount(item) }}</span>
            </template>
            <template #item.next_milestone="{ item }">
              <template v-if="item.next_milestone">
                <span class="board-ms">{{ item.next_milestone.title }}</span>
                <span class="text-caption text-medium-emphasis ms-1">
                  · {{ fmtDate(item.next_milestone.due_date) }}
                </span>
              </template>
              <span v-else class="text-medium-emphasis">暂无</span>
            </template>
            <template #item.state="{ item }">
              <span class="d-inline-flex align-center ga-2">
                <span
                  class="status-dot"
                  :class="isStale(item) ? 'status-dot--warn' : 'status-dot--ok'"
                />
                <span class="t-body c-text">{{ isStale(item) ? '停滞' : '活跃' }}</span>
              </span>
            </template>
            <!-- Expandable row: the team's 一页纸总结. -->
            <template #expanded-row="{ columns, item }">
              <tr>
                <td :colspan="columns.length" class="py-3">
                  <div class="t-eyebrow mb-2">一页纸总结</div>
                  <div v-if="item.summary" class="t-body">
                    {{ item.summary }}
                  </div>
                  <div v-else class="t-body c-faint">
                    芝士还没写总结
                  </div>
                </td>
              </tr>
            </template>
          </v-data-table>
        </v-card>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
.board-page {
  background: var(--canvas);
}

/* ---- Quiet data table: faint header, hairline rows, mono numbers ---- */
.board-table :deep(thead th) {
  font-size: 12px !important;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--faint) !important;
  background: var(--fill);
  border-bottom: 1px solid var(--line-2) !important;
}
.board-table :deep(tbody td) {
  border-bottom: 1px solid var(--line) !important;
  font-size: 14px;
  color: var(--text);
}
.board-table :deep(tbody tr:hover) {
  background: var(--fill);
}
.board-name {
  font-weight: 500;
  color: var(--ink);
}
.board-num {
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum';
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--faint);
}
.board-tag {
  font-size: 12px;
  color: var(--muted);
  background: var(--fill);
  padding: 1px 8px;
  border-radius: 6px;
}
.board-ms {
  font-size: 14px;
  color: var(--text);
}
</style>
