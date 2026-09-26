<script setup lang="ts">
// 工作方法：这个项目存下来的做法。确认过的那一版会带进之后每个房间的 AI 队友，所以
// 芝士整理出来、或者改过的，都要人在这里读一遍、点确认才算数。
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision } from '../api'
import type { Topic } from '../cx_types'

import { computed, reactive, ref, watch } from 'vue'

import {
  confirmProjectSkill,
  createProjectSkill,
  deleteProjectSkill,
  getProjectSkill,
  listProjectSkills,
  listTopics,
  restoreProjectSkill,
  updateProjectSkill,
} from '../api'

import { t } from '@/i18n'
import ProjectPage from '@/views/workspace/ProjectPage.vue'

const props = defineProps<{ projectId: string }>()

const skills = ref<ProjectSkill[]>([])
const rooms = ref<Topic[]>([])
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const busy = ref('')
const history = ref<{ skill: ProjectSkill; revisions: ProjectSkillRevision[] } | null>(null)
const viewing = ref<ProjectSkillRevision | null>(null)
const confirmingDelete = ref<ProjectSkill | null>(null)

const drafts = computed(() => skills.value.filter((s) => s.state === 'draft'))
const active = computed(() => skills.value.filter((s) => s.state === 'active'))

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—'
}

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const [listed, topics] = await Promise.all([listProjectSkills(projectId), listTopics(projectId)])
    if (props.projectId !== projectId) return
    skills.value = listed.data
    rooms.value = topics.data.filter((tp) => tp.status !== 'archived')
  } catch (e) {
    if (props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : '未能读取工作方法'
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

function replace(row: ProjectSkill) {
  skills.value = skills.value.map((s) => (s.id === row.id ? row : s))
}

async function confirm(s: ProjectSkill) {
  busy.value = `${s.id}:confirm`
  actionError.value = ''
  try {
    replace(await confirmProjectSkill(s.id))
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '没有确认成功'
  } finally {
    busy.value = ''
  }
}

// 芝士改过的那一份不要了：回到正在用的那一版，改动作废。
async function discard(s: ProjectSkill) {
  busy.value = `${s.id}:discard`
  actionError.value = ''
  try {
    replace(await restoreProjectSkill(s.id, s.shipped_revision))
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '没有放弃成功'
  } finally {
    busy.value = ''
  }
}

async function openHistory(s: ProjectSkill) {
  actionError.value = ''
  try {
    const { revisions, ...fresh } = await getProjectSkill(s.id)
    replace(fresh)
    history.value = { skill: fresh, revisions }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能读取历史版本'
  }
}

async function restore(revision: number) {
  if (!history.value) return
  const s = history.value.skill
  busy.value = `${s.id}:restore:${revision}`
  try {
    await restoreProjectSkill(s.id, revision)
    const { revisions, ...fresh } = await getProjectSkill(s.id)
    replace(fresh)
    history.value = { skill: fresh, revisions }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '没有恢复成功'
  } finally {
    busy.value = ''
  }
}

async function remove(s: ProjectSkill) {
  confirmingDelete.value = null
  busy.value = `${s.id}:delete`
  actionError.value = ''
  try {
    await deleteProjectSkill(s.id)
    skills.value = skills.value.filter((x) => x.id !== s.id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能删除'
  } finally {
    busy.value = ''
  }
}

// ── 新建 / 编辑 ────────────────────────────────────────────────────────────
const editing = ref<ProjectSkill | 'new' | null>(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  room: '',
  name: '',
  title: '',
  description: '',
  inputs: '',
  steps: '',
  outputs: '',
  files: [] as { path: string; content: string }[],
})

function startNew() {
  Object.assign(form, {
    room: rooms.value[0]?.id ?? '',
    name: '',
    title: '',
    description: '',
    inputs: '',
    steps: '',
    outputs: '',
    files: [],
  })
  formError.value = ''
  editing.value = 'new'
}

function startEdit(s: ProjectSkill) {
  Object.assign(form, {
    room: '',
    name: s.name,
    title: s.title,
    description: s.description,
    inputs: s.inputs,
    steps: s.steps,
    outputs: s.outputs,
    files: Object.entries(s.files).map(([path, content]) => ({ path, content })),
  })
  formError.value = ''
  editing.value = s
}

function content(): ProjectSkillContent {
  return {
    title: form.title,
    description: form.description,
    inputs: form.inputs,
    steps: form.steps,
    outputs: form.outputs,
    files: Object.fromEntries(form.files.filter((f) => f.path.trim()).map((f) => [f.path.trim(), f.content])),
  }
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    if (editing.value === 'new') {
      if (!form.room) throw new Error('先选一个房间：工作方法记在它名下，整个项目都能用')
      const created = await createProjectSkill(form.room, { name: form.name.trim(), ...content() })
      skills.value = [...skills.value, created]
    } else if (editing.value) {
      replace(await updateProjectSkill(editing.value.id, content()))
    }
    editing.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '没有保存成功'
  } finally {
    saving.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    skills.value = []
    history.value = null
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <ProjectPage :title="t('navigation.project.skills')">
    <template #actions>
      <v-btn :loading="loading" @click="load">刷新</v-btn>
      <v-btn color="primary" :disabled="!rooms.length" @click="startNew">新建</v-btn>
    </template>
    <div>
      <p class="t-body c-muted mb-6">
        一次做得满意的工作，可以让芝士整理成工作方法：要什么输入、按什么步骤和规则做、交出什么。确认过的版本会带进这个项目之后的每个房间，
        在房间里说「按周报方法做一份」就能用
      </p>

      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>
      <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

      <div v-if="loading && !skills.length" class="py-8 text-center" role="status" aria-label="读取工作方法">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else>
        <section v-if="drafts.length" class="mb-6">
          <h2 class="t-section mb-2">等你确认</h2>
          <ul class="skill-list">
            <li v-for="s in drafts" :key="s.id" class="skill-row skill-row--draft" :data-skill="s.id">
              <div class="skill-row__head">
                <div class="skill-row__id">
                  <div class="t-body skill-row__title">{{ s.title }}</div>
                  <div class="t-meta c-faint">
                    {{ s.name }} · 由 {{ s.proposed_by }} 整理
                    <template v-if="s.shipped_revision">· 正在用的是第 {{ s.shipped_revision }} 版，这是改动</template>
                  </div>
                </div>
                <v-chip size="small" color="warning" variant="tonal">待确认</v-chip>
              </div>
              <dl class="skill-row__spec t-meta">
                <dt>用途</dt>
                <dd>{{ s.description }}</dd>
                <dt>需要的输入</dt>
                <dd>{{ s.inputs || '—' }}</dd>
                <dt>步骤与规则</dt>
                <dd>{{ s.steps }}</dd>
                <dt>输出要求</dt>
                <dd>{{ s.outputs || '—' }}</dd>
                <template v-if="Object.keys(s.files).length">
                  <dt>配套文件</dt>
                  <dd>{{ Object.keys(s.files).join('、') }}</dd>
                </template>
              </dl>
              <div class="skill-row__actions">
                <v-btn
                  size="small"
                  color="primary"
                  variant="flat"
                  :loading="busy === `${s.id}:confirm`"
                  @click="confirm(s)"
                >
                  确认保存
                </v-btn>
                <v-btn size="small" variant="text" @click="startEdit(s)">修改</v-btn>
                <v-btn
                  v-if="s.shipped_revision"
                  size="small"
                  variant="text"
                  color="on-surface-variant"
                  :loading="busy === `${s.id}:discard`"
                  @click="discard(s)"
                >
                  放弃改动
                </v-btn>
                <v-btn
                  v-if="!s.shipped_revision"
                  size="small"
                  variant="text"
                  color="on-surface-variant"
                  @click="confirmingDelete = s"
                >
                  不要了
                </v-btn>
              </div>
            </li>
          </ul>
        </section>

        <ul v-if="active.length" class="skill-list">
          <li v-for="s in active" :key="s.id" class="skill-row" :data-skill="s.id">
            <div class="skill-row__head">
              <div class="skill-row__id">
                <div class="t-body skill-row__title">{{ s.title }}</div>
                <div class="t-meta c-faint">
                  {{ s.name }} · 第 {{ s.shipped_revision }} 版 · {{ s.confirmed_by }} 确认于 {{ fmt(s.confirmed_at) }}
                </div>
                <div class="t-meta c-muted mt-1">{{ s.description }}</div>
              </div>
            </div>
            <div class="skill-row__actions">
              <v-btn size="small" variant="text" @click="startEdit(s)">修改</v-btn>
              <v-btn size="small" variant="text" @click="openHistory(s)">历史版本</v-btn>
              <v-btn size="small" variant="text" color="on-surface-variant" @click="confirmingDelete = s">删除</v-btn>
            </div>
          </li>
        </ul>

        <div v-if="!skills.length && !loadError" class="py-8 text-center">
          <p class="t-body c-muted">还没有存下来的工作方法</p>
          <p class="t-meta c-faint mt-1">做完一次满意的工作后，在房间里说「把这次的做法存成工作方法」</p>
        </div>
      </template>
    </div>

    <v-dialog :model-value="!!editing" max-width="640" @update:model-value="editing = null">
      <v-card v-if="editing">
        <v-card-title class="t-dialog-title">
          {{ editing === 'new' ? '新建工作方法' : `修改「${editing.title}」` }}
        </v-card-title>
        <v-card-text>
          <template v-if="editing === 'new'">
            <v-select
              v-model="form.room"
              autocomplete="off"
              :items="rooms"
              item-title="title"
              item-value="id"
              label="记在哪个房间名下"
            />
            <v-text-field
              v-model="form.name"
              autocomplete="off"
              label="英文名（小写、连字符）"
              placeholder="例如 weekly-report"
            />
          </template>
          <v-text-field v-model="form.title" autocomplete="off" label="名称" placeholder="例如：项目周报" />
          <v-textarea v-model="form.description" autocomplete="off" label="用途：什么时候用它" rows="2" auto-grow />
          <v-textarea v-model="form.inputs" autocomplete="off" label="需要的输入" rows="2" auto-grow />
          <v-textarea v-model="form.steps" autocomplete="off" label="步骤与规则" rows="5" auto-grow />
          <v-textarea v-model="form.outputs" autocomplete="off" label="输出要求" rows="2" auto-grow />
          <div class="t-meta c-muted mb-2">配套文件（脚本、模板说明等文本文件）</div>
          <div v-for="(f, i) in form.files" :key="i" class="skill-file">
            <v-text-field v-model="f.path" autocomplete="off" label="路径" placeholder="scripts/check.py" />
            <v-textarea
              v-model="f.content"
              autocomplete="off"
              label="内容"
              rows="3"
              auto-grow
              class="skill-file__body"
            />
            <v-btn size="small" variant="text" color="on-surface-variant" @click="form.files.splice(i, 1)">移除</v-btn>
          </div>
          <v-btn size="small" variant="text" @click="form.files.push({ path: '', content: '' })">添加文件</v-btn>
          <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="editing = null">取消</v-btn>
          <v-btn variant="text" color="primary" :loading="saving" @click="save">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!history" max-width="640" @update:model-value="history = null">
      <v-card v-if="history">
        <v-card-title class="t-dialog-title">「{{ history.skill.title }}」的历史版本</v-card-title>
        <v-card-text>
          <ol class="skill-list">
            <li v-for="r in history.revisions" :key="r.revision" class="skill-row" :data-revision="r.revision">
              <div class="skill-row__head">
                <div class="skill-row__id t-meta">
                  第 {{ r.revision }} 版 · {{ r.note }} · {{ r.confirmed_by }} · {{ fmt(r.created_at) }}
                </div>
                <v-chip
                  v-if="r.revision === history.skill.shipped_revision"
                  size="x-small"
                  color="success"
                  variant="tonal"
                >
                  正在用
                </v-chip>
              </div>
              <div class="skill-row__actions">
                <v-btn size="small" variant="text" @click="viewing = viewing === r ? null : r">
                  {{ viewing === r ? '收起' : '查看' }}
                </v-btn>
                <v-btn
                  v-if="r.revision !== history.skill.shipped_revision"
                  size="small"
                  variant="text"
                  :loading="busy === `${history.skill.id}:restore:${r.revision}`"
                  @click="restore(r.revision)"
                >
                  恢复到这一版
                </v-btn>
              </div>
              <dl v-if="viewing === r" class="skill-row__spec t-meta">
                <dt>用途</dt>
                <dd>{{ r.content.description }}</dd>
                <dt>需要的输入</dt>
                <dd>{{ r.content.inputs || '—' }}</dd>
                <dt>步骤与规则</dt>
                <dd>{{ r.content.steps }}</dd>
                <dt>输出要求</dt>
                <dd>{{ r.content.outputs || '—' }}</dd>
              </dl>
            </li>
          </ol>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="history = null">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!confirmingDelete" max-width="420" @update:model-value="confirmingDelete = null">
      <v-card v-if="confirmingDelete">
        <v-card-title class="t-dialog-title">删除「{{ confirmingDelete.title }}」</v-card-title>
        <v-card-text class="t-body">删除后之后的房间不再带着它，历史版本也一起删除</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirmingDelete = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="remove(confirmingDelete)">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </ProjectPage>
</template>

<style scoped>
.skill-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-row {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.skill-row--draft {
  border-color: rgb(var(--v-theme-warning));
}
.skill-row__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.skill-row__id {
  flex: 1;
  min-width: 0;
}
.skill-row__title {
  color: var(--text);
}
.skill-row__spec {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
  margin: 8px 0;
}
.skill-row__spec dt {
  color: var(--faint);
}
.skill-row__spec dd {
  margin: 0;
  white-space: pre-wrap;
}
.skill-row__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}
.skill-file {
  border-left: 2px solid var(--line);
  padding-left: 8px;
  margin-bottom: 8px;
}
.skill-file__body :deep(textarea) {
  font-family: var(--font-mono, monospace);
}
</style>
