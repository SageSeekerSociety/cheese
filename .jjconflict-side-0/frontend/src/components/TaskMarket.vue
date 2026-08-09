<script setup lang="ts">
import type { MarketTask, Project, TaskApplication } from '../cx_types'

import { computed, onMounted, reactive, ref } from 'vue'

import { applyMarketTask, decideTaskApplication, getMarketTasks, listProjects, listTaskApplications } from '../api'
import { myHandle } from '../me'

// 题目匹配 (spec §13 阶段 6): Spaces publish 题目 (Task Templates) here; a team
// applies with one of its projects (应征); the Space accepts → the project is
// linked to a new Task under the template (protocol signed, spec §4.2).

const tasks = ref<MarketTask[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('')

// My projects come first in the apply selector (团队 = a project's owner/lead).
const projects = ref<Project[]>([])

// Apply dialog state.
const applyDialog = ref(false)
const applyTarget = ref<MarketTask | null>(null)
const applyProjectId = ref<string | null>(null)
const applyPitch = ref('')
const applying = ref(false)
const applyError = ref<string | null>(null)

// Space-side: applications per template, loaded when a card is expanded.
const expanded = reactive<Record<string, boolean>>({})
const applications = reactive<Record<string, TaskApplication[]>>({})
const appsLoading = reactive<Record<string, boolean>>({})
const deciding = reactive<Record<string, boolean>>({})

const projectOptions = computed(() => {
  const mine = projects.value.filter((p) => p.owner_handle === myHandle())
  const others = projects.value.filter((p) => p.owner_handle !== myHandle())
  return [...mine, ...others].map((p) => ({
    title: p.owner_handle === myHandle() ? `${p.name}（我的）` : p.name,
    value: p.id,
  }))
})

const STATUS_LABEL: Record<TaskApplication['status'], string> = {
  pending: '待定',
  accepted: '已接受',
  declined: '已婉拒',
}
const STATUS_COLOR: Record<TaskApplication['status'], string> = {
  pending: 'warning',
  accepted: 'success',
  declined: 'default',
}

function packEntries(t: MarketTask): string[] {
  return Object.entries(t.resource_pack).map(([k, v]) => `${k}: ${String(v)}`)
}

function conditionEntries(t: MarketTask): string[] {
  return t.conditions.map((c) =>
    Object.entries(c)
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join(' · ')
  )
}

async function load() {
  loading.value = true
  error.value = null
  try {
    tasks.value = (await getMarketTasks(keyword.value.trim() || undefined)).data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载题目失败'
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  try {
    projects.value = (await listProjects()).data
  } catch {
    // The apply dialog will just show an empty selector.
  }
}

function openApply(task: MarketTask) {
  applyTarget.value = task
  applyProjectId.value = null
  applyPitch.value = ''
  applyError.value = null
  applyDialog.value = true
}

async function submitApply() {
  if (!applyTarget.value || !applyProjectId.value) return
  applying.value = true
  applyError.value = null
  try {
    await applyMarketTask(applyTarget.value.id, applyProjectId.value, applyPitch.value.trim())
    const id = applyTarget.value.id
    applyDialog.value = false
    // Refresh the applicant list if the card is open.
    if (expanded[id]) await loadApplications(id)
  } catch (e) {
    applyError.value = e instanceof Error ? e.message : '应征失败'
  } finally {
    applying.value = false
  }
}

async function toggleApplications(templateId: string) {
  expanded[templateId] = !expanded[templateId]
  if (expanded[templateId]) await loadApplications(templateId)
}

async function loadApplications(templateId: string) {
  appsLoading[templateId] = true
  try {
    applications[templateId] = (await listTaskApplications(templateId)).data
  } catch {
    applications[templateId] = []
  } finally {
    appsLoading[templateId] = false
  }
}

async function decide(app: TaskApplication, decision: 'accept' | 'decline') {
  deciding[app.id] = true
  try {
    await decideTaskApplication(app.id, decision, myHandle())
    await loadApplications(app.template_id)
  } finally {
    deciding[app.id] = false
  }
}

onMounted(() => {
  load()
  loadProjects()
})
</script>

<template>
  <div>
    <div class="d-flex align-center mb-4" style="gap: 12px">
      <v-text-field
        v-model="keyword"
        density="compact"
        variant="outlined"
        prepend-inner-icon="mdi-magnify"
        placeholder="按题目、简介或机构搜索…"
        hide-details
        clearable
        style="max-width: 380px"
        @keyup.enter="load"
        @click:clear="(keyword = ''), load()"
      />
      <v-btn size="small" variant="tonal" color="primary" @click="load">搜索</v-btn>
    </div>

    <div v-if="loading" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <v-alert v-else-if="error" type="error" density="comfortable">{{ error }}</v-alert>
    <v-alert v-else-if="tasks.length === 0" type="info" variant="tonal" density="comfortable">
      市场上还没有发布中的题目。机构在 Space 里把 Task Template 发布后会出现在这里。
    </v-alert>

    <div v-else class="task-grid">
      <article v-for="t in tasks" :key="t.id" class="task-card">
        <div class="task-card__space"><v-icon size="14" class="me-1">mdi-domain</v-icon>{{ t.space_name }}</div>
        <h3 class="task-card__title">{{ t.name }}</h3>
        <p v-if="t.description" class="task-card__desc c-muted">{{ t.description }}</p>

        <div v-if="packEntries(t).length" class="task-card__section">
          <div class="task-card__label">资源包</div>
          <div class="task-card__chips">
            <span v-for="e in packEntries(t)" :key="e" class="task-chip">{{ e }}</span>
          </div>
        </div>
        <div v-if="conditionEntries(t).length" class="task-card__section">
          <div class="task-card__label">条件</div>
          <div class="task-card__chips">
            <span v-for="e in conditionEntries(t)" :key="e" class="task-chip task-chip--cond">{{ e }}</span>
          </div>
        </div>
        <div v-if="t.default_role" class="task-card__section">
          <div class="task-card__label">默认角色</div>
          <span class="task-chip">{{ t.default_role }}</span>
        </div>

        <div class="task-card__actions">
          <v-btn size="small" color="primary" variant="flat" @click="openApply(t)"> 应征 </v-btn>
          <v-btn
            size="small"
            variant="text"
            :append-icon="expanded[t.id] ? 'mdi-chevron-up' : 'mdi-chevron-down'"
            @click="toggleApplications(t.id)"
          >
            应征列表
          </v-btn>
        </div>

        <div v-if="expanded[t.id]" class="task-card__apps">
          <div v-if="appsLoading[t.id]" class="py-2 d-flex justify-center">
            <v-progress-circular indeterminate size="18" width="2" />
          </div>
          <div v-else-if="!applications[t.id]?.length" class="c-faint task-card__apps-empty">还没有团队应征。</div>
          <div v-for="a in applications[t.id] ?? []" v-else :key="a.id" class="app-row">
            <div class="app-row__main">
              <div class="app-row__head">
                <span class="app-row__project">{{ a.project_name }}</span>
                <v-chip size="x-small" :color="STATUS_COLOR[a.status]" variant="tonal">
                  {{ STATUS_LABEL[a.status] }}
                </v-chip>
              </div>
              <div v-if="a.pitch" class="app-row__pitch c-muted">{{ a.pitch }}</div>
              <div v-if="a.decided_by" class="app-row__decided c-faint">由 {{ a.decided_by }} 决定</div>
            </div>
            <div v-if="a.status === 'pending'" class="app-row__actions">
              <v-btn
                size="x-small"
                color="success"
                variant="tonal"
                :loading="deciding[a.id]"
                @click="decide(a, 'accept')"
              >
                接受
              </v-btn>
              <v-btn size="x-small" variant="text" :loading="deciding[a.id]" @click="decide(a, 'decline')">
                婉拒
              </v-btn>
            </div>
          </div>
        </div>
      </article>
    </div>

    <v-dialog v-model="applyDialog" max-width="480">
      <v-card v-if="applyTarget">
        <v-card-title class="text-subtitle-1"> 应征「{{ applyTarget.name }}」 </v-card-title>
        <v-card-text>
          <v-select
            v-model="applyProjectId"
            :items="projectOptions"
            label="用哪个项目应征"
            density="comfortable"
            variant="outlined"
          />
          <v-textarea
            v-model="applyPitch"
            label="团队自荐（为什么我们合适）"
            rows="3"
            density="comfortable"
            variant="outlined"
            hide-details
          />
          <v-alert v-if="applyError" type="error" density="compact" variant="tonal" class="mt-3">
            {{ applyError }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="applyDialog = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!applyProjectId" :loading="applying" @click="submitApply">
            提交应征
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}
.task-card {
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 12px;
  padding: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  transition:
    box-shadow 0.15s,
    border-color 0.15s;
}
.task-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
}
.task-card__space {
  display: inline-flex;
  align-items: center;
  font-size: 0.72rem;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
  margin-bottom: 6px;
}
.task-card__title {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.3;
}
.task-card__desc {
  font-size: 0.82rem;
  line-height: 1.55;
  margin-top: 4px;
}
.task-card__section {
  margin-top: 10px;
}
.task-card__label {
  font-size: 0.68rem;
  color: var(--faint);
  margin-bottom: 4px;
}
.task-card__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.task-chip {
  font-size: 0.7rem;
  padding: 1px 8px;
  border-radius: 10px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.08);
}
.task-chip--cond {
  color: var(--muted);
  background: rgba(var(--v-border-color), 0.18);
}
.task-card__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
}
.task-card__apps {
  margin-top: 10px;
  border-top: 1px dashed rgba(var(--v-border-color), 0.6);
  padding-top: 8px;
}
.task-card__apps-empty {
  font-size: 0.76rem;
  padding: 4px 0;
}
.app-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
}
.app-row + .app-row {
  border-top: 1px solid rgba(var(--v-border-color), 0.35);
}
.app-row__head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.app-row__project {
  font-size: 0.84rem;
  font-weight: 600;
}
.app-row__pitch {
  font-size: 0.78rem;
  line-height: 1.5;
  margin-top: 2px;
}
.app-row__decided {
  font-size: 0.7rem;
  margin-top: 2px;
}
.app-row__actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
</style>
