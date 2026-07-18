<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { getCalendar, getProject, listMilestones } from '../api'
import type { MilestoneFull } from '../cx_types'

// 时间维度 (spec §7.2): a clean deadline list with countdowns, plus the done
// milestones shown faded.
const props = defineProps<{ projectId: string }>()

const projectName = ref<string>('')
const upcoming = ref<MilestoneFull[]>([])
const allMilestones = ref<MilestoneFull[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// Done milestones (status === 'done'), kept separate to render faded.
const done = computed<MilestoneFull[]>(() =>
  allMilestones.value.filter((m) => m.status === 'done'),
)

function fmtDate(d: string | null): string {
  if (!d) return '待定'
  return d.length >= 10 ? d.slice(0, 10) : d
}

// Whole-day countdown from now to the due date.
function daysLeft(d: string | null): number | null {
  if (!d) return null
  const due = new Date(d).getTime()
  if (Number.isNaN(due)) return null
  const ms = due - Date.now()
  return Math.ceil(ms / (1000 * 60 * 60 * 24))
}

function countdownLabel(d: string | null): string {
  const n = daysLeft(d)
  if (n === null) return '日期待定'
  if (n < 0) return `已过期 ${-n} 天`
  if (n === 0) return '今天截止'
  return `还剩 ${n} 天`
}

// Countdown urgency → a neutral/semantic dot class (status as dot, not a chip).
function countdownDotClass(d: string | null): string {
  const n = daysLeft(d)
  if (n === null) return 'status-dot--muted'
  if (n < 0) return 'status-dot--danger'
  if (n <= 3) return 'status-dot--warn'
  return 'status-dot--ok'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [cal, all] = await Promise.all([
      getCalendar(props.projectId),
      listMilestones(props.projectId),
    ])
    upcoming.value = cal.data
    allMilestones.value = all.data
    try {
      const project = await getProject(props.projectId)
      projectName.value = project.name
    } catch {
      projectName.value = ''
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载日历失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.projectId, load)
onMounted(load)
</script>

<template>
  <div class="calendar-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 820px">
      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable">
        {{ error }}
      </v-alert>

      <template v-else>
        <div class="mb-6">
          <div class="t-eyebrow mb-1">日历 · 时间维度</div>
          <h1 class="t-page-title">{{ projectName || '项目日历' }}</h1>
        </div>

        <!-- Upcoming deadlines as a timeline -->
        <v-card class="mb-6">
          <v-card-title class="d-flex align-center ga-2 t-title pt-4">
            <v-icon size="19" class="c-faint">mdi-calendar-clock</v-icon>
            即将到来
            <span v-if="upcoming.length" class="chip-neutral">{{ upcoming.length }}</span>
          </v-card-title>
          <v-card-text>
            <div v-if="upcoming.length === 0" class="c-faint t-body py-2">
              暂无即将到来的里程碑
            </div>
            <v-list v-else density="comfortable" class="py-0">
              <v-list-item v-for="m in upcoming" :key="m.id" class="px-0">
                <template #prepend>
                  <span class="status-dot me-3" :class="countdownDotClass(m.due_date)" />
                </template>
                <v-list-item-title style="font-weight: 500; color: var(--ink)">
                  {{ m.title }}
                </v-list-item-title>
                <v-list-item-subtitle class="c-muted">
                  截止 {{ fmtDate(m.due_date) }}
                </v-list-item-subtitle>
                <template #append>
                  <span
                    class="d-inline-flex align-center ga-1"
                    :class="countdownDotClass(m.due_date) === 'status-dot--danger' ? 'c-danger' : 'c-muted'"
                    style="font-size: 12.5px; font-family: var(--font-mono)"
                  >
                    {{ countdownLabel(m.due_date) }}
                  </span>
                </template>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>

        <!-- Done milestones (faded) -->
        <v-card v-if="done.length">
          <v-card-title class="d-flex align-center ga-2 t-title pt-4">
            <v-icon size="19" class="c-faint">mdi-check-circle-outline</v-icon>
            已完成
          </v-card-title>
          <v-card-text>
            <v-list density="compact" class="py-0 done-list">
              <v-list-item v-for="m in done" :key="m.id" class="px-0">
                <template #prepend>
                  <span class="status-dot status-dot--ok me-3" />
                </template>
                <v-list-item-title>{{ m.title }}</v-list-item-title>
                <template #append>
                  <span class="t-meta">{{ fmtDate(m.due_date) }}</span>
                </template>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
.calendar-page {
  background: var(--canvas);
}
.done-list {
  opacity: 0.55;
}
</style>
