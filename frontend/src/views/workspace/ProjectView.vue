<!-- 知是 2.0「项目」工作区：左侧文档树、右侧 markdown 编辑器（文档即节点）。
     未选项目时先展示项目选择器；选中项目后 projectId 落到路由参数上以便刷新/回退保持。
     编辑器为纯 markdown textarea + 预览切换（复用应用现有 marked+DOMPurify），
     保存 = 手动「保存」按钮 + 停止输入 1.2s 后防抖自动保存（PUT 整篇正文）。 -->
<template>
  <div class="pv-shell">
    <!-- 项目选择器 -->
    <div v-if="!projectId" class="pv-picker">
      <div class="pv-picker__box">
        <div v-if="loadingProjects" class="pv-picker__hint">加载中…</div>

        <!-- 空态：引导创建第一个项目 -->
        <div v-else-if="!projects.length" class="pv-empty">
          <v-icon icon="mdi-folder-plus-outline" size="56" class="pv-empty__ic" />
          <div class="pv-empty__title">还没有项目</div>
          <div class="pv-empty__sub">创建你的第一个项目，开始用文档整理想法。</div>
          <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreateProject">创建项目</v-btn>
        </div>

        <template v-else>
          <div class="pv-picker__title">
            <span>选择一个项目</span>
            <v-spacer />
            <v-btn icon="mdi-plus" size="small" variant="text" title="创建项目" @click="openCreateProject" />
          </div>
          <v-list class="pv-picker__list" density="compact">
            <v-list-item
              v-for="p in projects"
              :key="p.id"
              :title="p.name"
              :subtitle="p.team?.name || p.description"
              prepend-icon="mdi-folder-outline"
              rounded="lg"
              @click="pickProject(p.id)"
            >
              <template #append>
                <v-btn
                  icon="mdi-pencil-outline"
                  size="x-small"
                  variant="text"
                  title="重命名"
                  @click.stop="openRenameProject(p)"
                />
                <v-btn
                  icon="mdi-delete-outline"
                  size="x-small"
                  variant="text"
                  title="删除项目"
                  @click.stop="openDeleteProject(p)"
                />
              </template>
            </v-list-item>
          </v-list>
        </template>
      </div>
    </div>

    <template v-else>
      <!-- 左：文档树 -->
      <aside class="pv-tree">
        <div class="pv-tree__hd">
          <v-btn variant="text" size="small" density="comfortable" prepend-icon="mdi-swap-horizontal" @click="changeProject">
            {{ currentProjectName || '项目' }}
          </v-btn>
          <v-spacer />
          <v-btn icon="mdi-account-group-outline" size="small" variant="text" title="项目成员" @click="membersOpen = true" />
          <v-btn icon="mdi-laptop" size="small" variant="text" title="项目设备" @click="devicesOpen = true" />
          <v-btn icon="mdi-plus" size="small" variant="text" title="新建根文档" @click="createDoc(null)" />
        </div>
        <div class="pv-tree__scroll">
          <div v-if="loadingTree" class="pv-tree__hint">加载中…</div>
          <template v-else>
            <DocumentTreeNode
              v-for="node in visibleTree"
              :key="node.id"
              :node="node"
              :active-id="activeId"
              :depth="0"
              :show-archived="showArchived"
              @select="openDoc"
              @add-child="createDoc"
              @rename="startRename"
              @delete="confirmDelete"
              @toggle-archive="toggleArchive"
            />
            <div v-if="!visibleTree.length" class="pv-tree__hint">还没有文档，点右上「＋」新建。</div>
          </template>
        </div>
        <div class="pv-tree__ft">
          <v-switch v-model="showArchived" label="显示已归档" density="compact" hide-details color="primary" />
        </div>
      </aside>

      <!-- 右：markdown 编辑器 -->
      <section class="pv-editor">
        <template v-if="activeId">
          <div class="pv-editor__hd">
            <span class="pv-editor__title">{{ activeTitle }}</span>
            <v-spacer />
            <span class="pv-editor__status" :class="statusClass">{{ statusText }}</span>
            <v-btn-toggle v-model="viewMode" density="comfortable" variant="outlined" divided mandatory class="mx-2">
              <v-btn value="edit" size="small">编辑</v-btn>
              <v-btn value="split" size="small">分屏</v-btn>
              <v-btn value="preview" size="small">预览</v-btn>
            </v-btn-toggle>
            <v-btn size="small" color="primary" variant="flat" :loading="saving" :disabled="!dirty" @click="save">保存</v-btn>
          </div>
          <div class="pv-editor__body">
            <textarea
              v-if="viewMode !== 'preview'"
              v-model="content"
              class="pv-editor__ta"
              :class="{ 'pv-editor__ta--split': viewMode === 'split' }"
              placeholder="用 Markdown 书写…"
              spellcheck="false"
              @input="onInput"
            />
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div v-if="viewMode !== 'edit'" class="pv-editor__preview markdown-body" v-html="renderedHtml" />
          </div>
        </template>
        <div v-else class="pv-editor__blank text-medium-emphasis">从左侧选择或新建一篇文档。</div>
      </section>
    </template>

    <!-- 项目成员管理侧栏（含项目 agent 的创建/停止） -->
    <ProjectMembersPanel v-model:open="membersOpen" :project-id="projectId" />

    <!-- 项目设备管理侧栏（绑定/解绑设备） -->
    <ProjectDevicesPanel v-model:open="devicesOpen" :project-id="projectId" />

    <!-- 创建项目对话框 -->
    <v-dialog v-model="createProjectOpen" width="460">
      <v-card>
        <v-card-title>创建项目</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newProjectName"
            label="名称"
            variant="outlined"
            density="comfortable"
            autofocus
            class="mb-2"
            hide-details
            @keydown.enter.prevent="submitCreateProject"
          />
          <v-textarea
            v-model="newProjectDesc"
            label="简介（可选）"
            variant="outlined"
            density="comfortable"
            rows="3"
            auto-grow
            hide-details
          />
          <div v-if="createProjectError" class="pv-err">{{ createProjectError }}</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="createProjectOpen = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creatingProject"
            :disabled="!newProjectName.trim()"
            @click="submitCreateProject"
          >
            创建
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 项目重命名对话框 -->
    <v-dialog v-model="renameProjectOpen" width="460">
      <v-card>
        <v-card-title>重命名项目</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="renameProjectName"
            label="名称"
            variant="outlined"
            density="comfortable"
            autofocus
            class="mb-2"
            hide-details
            @keydown.enter.prevent="submitRenameProject"
          />
          <v-textarea
            v-model="renameProjectDesc"
            label="简介（可选）"
            variant="outlined"
            density="comfortable"
            rows="3"
            auto-grow
            hide-details
          />
          <div v-if="renameProjectError" class="pv-err">{{ renameProjectError }}</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="renameProjectOpen = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="renamingProject"
            :disabled="!renameProjectName.trim()"
            @click="submitRenameProject"
          >
            保存
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 项目删除确认 -->
    <v-dialog v-model="deleteProjectOpen" width="440">
      <v-card>
        <v-card-title>删除项目</v-card-title>
        <v-card-text>
          确定删除项目「{{ deleteProjectTarget?.name || '' }}」？此操作不可恢复。
          <div v-if="deleteProjectError" class="pv-err">{{ deleteProjectError }}</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteProjectOpen = false">取消</v-btn>
          <v-btn color="error" variant="flat" :loading="deletingProject" @click="submitDeleteProject">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 重命名对话框 -->
    <v-dialog v-model="renameOpen" width="420">
      <v-card>
        <v-card-title>重命名文档</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="renameTitle"
            label="标题"
            variant="outlined"
            density="comfortable"
            autofocus
            hide-details
            @keydown.enter.prevent="applyRename"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="renameOpen = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!renameTitle.trim()" @click="applyRename">确定</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 删除确认 -->
    <v-dialog v-model="deleteOpen" width="420">
      <v-card>
        <v-card-title>删除文档</v-card-title>
        <v-card-text>
          确定删除「{{ deleteTarget?.title || '无标题' }}」吗？其子文档会上提到它的父级。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteOpen = false">取消</v-btn>
          <v-btn color="error" variant="flat" @click="applyDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { DocumentTreeNode as TreeNode } from '@/network/api/documents'

import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  patchDocument,
  saveDocument,
} from '@/network/api/documents'
import { ProjectsApi } from '@/network/api/projects'
import DocumentTreeNode from '@/views/workspace/components/DocumentTreeNode.vue'
import ProjectDevicesPanel from '@/views/workspace/components/ProjectDevicesPanel.vue'
import ProjectMembersPanel from '@/views/workspace/components/ProjectMembersPanel.vue'

const route = useRoute()
const router = useRouter()

// ── 当前项目 ────────────────────────────────────────────────────────────────
const projectId = computed<number | null>(() => {
  const p = route.params.projectId
  const n = Array.isArray(p) ? Number(p[0]) : Number(p)
  return Number.isFinite(n) && n > 0 ? n : null
})

interface PickProject {
  id: number
  name: string
  description?: string
  team?: { name?: string }
}
const projects = ref<PickProject[]>([])
const loadingProjects = ref(false)
const currentProjectName = ref('')

// 项目成员管理侧栏开关。
const membersOpen = ref(false)
// 项目设备管理侧栏开关。
const devicesOpen = ref(false)

// 「我的项目」唯一真源：GET /projects/mine（含无小队的个人项目）。
const loadProjects = async () => {
  loadingProjects.value = true
  try {
    const res = await ProjectsApi.listMine()
    projects.value = (res.data.projects ?? []).map((p) => ({
      id: p.id,
      name: p.name,
      description: p.description,
      team: p.team ? { name: p.team.name } : undefined,
    }))
  } finally {
    loadingProjects.value = false
  }
}

// ── 创建项目 ────────────────────────────────────────────────────────────────
const createProjectOpen = ref(false)
const newProjectName = ref('')
const newProjectDesc = ref('')
const creatingProject = ref(false)
const createProjectError = ref('')

const openCreateProject = () => {
  newProjectName.value = ''
  newProjectDesc.value = ''
  createProjectError.value = ''
  createProjectOpen.value = true
}

const submitCreateProject = async () => {
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value) return
  creatingProject.value = true
  createProjectError.value = ''
  try {
    const res = await ProjectsApi.createMine({ name, description: newProjectDesc.value.trim() || undefined })
    createProjectOpen.value = false
    // 选中新建的项目 → 打开其（空）文档树。
    pickProject(res.data.project.id)
  } catch (e) {
    createProjectError.value = e instanceof Error ? e.message : '创建失败，请重试'
  } finally {
    creatingProject.value = false
  }
}

// ── 重命名 / 删除项目 ─────────────────────────────────────────────────────────
const renameProjectOpen = ref(false)
const renameProjectTarget = ref<PickProject | null>(null)
const renameProjectName = ref('')
const renameProjectDesc = ref('')
const renamingProject = ref(false)
const renameProjectError = ref('')

const openRenameProject = (p: PickProject) => {
  renameProjectTarget.value = p
  renameProjectName.value = p.name
  renameProjectDesc.value = p.description ?? ''
  renameProjectError.value = ''
  renameProjectOpen.value = true
}

const submitRenameProject = async () => {
  const target = renameProjectTarget.value
  const name = renameProjectName.value.trim()
  if (!target || !name || renamingProject.value) return
  renamingProject.value = true
  renameProjectError.value = ''
  try {
    const desc = renameProjectDesc.value.trim()
    await ProjectsApi.renameProject(target.id, { name, description: desc })
    // 就地更新列表。
    const local = projects.value.find((p) => p.id === target.id)
    if (local) {
      local.name = name
      local.description = desc
    }
    if (projectId.value === target.id) currentProjectName.value = name
    renameProjectOpen.value = false
  } catch (e) {
    renameProjectError.value = e instanceof Error ? e.message : '保存失败，请重试'
  } finally {
    renamingProject.value = false
  }
}

const deleteProjectOpen = ref(false)
const deleteProjectTarget = ref<PickProject | null>(null)
const deletingProject = ref(false)
const deleteProjectError = ref('')

const openDeleteProject = (p: PickProject) => {
  deleteProjectTarget.value = p
  deleteProjectError.value = ''
  deleteProjectOpen.value = true
}

const submitDeleteProject = async () => {
  const target = deleteProjectTarget.value
  if (!target || deletingProject.value) return
  deletingProject.value = true
  deleteProjectError.value = ''
  try {
    await ProjectsApi.deleteProject(target.id)
    projects.value = projects.value.filter((p) => p.id !== target.id)
    deleteProjectOpen.value = false
    // 若删除的是当前打开的项目，退回选择器。
    if (projectId.value === target.id) changeProject()
  } catch (e) {
    deleteProjectError.value = e instanceof Error ? e.message : '删除失败，请重试'
  } finally {
    deletingProject.value = false
  }
}

const pickProject = (id: number) => {
  router.push({ name: 'ProjectDocuments', params: { projectId: id }, query: { ...route.query } })
}
const changeProject = () => {
  router.push({ name: 'ProjectDocuments', params: {}, query: { ...route.query } })
}

// ── 文档树 ──────────────────────────────────────────────────────────────────
const tree = ref<TreeNode[]>([])
const loadingTree = ref(false)
const showArchived = ref(false)

const visibleTree = computed(() => (showArchived.value ? tree.value : tree.value.filter((n) => !n.archived)))

const loadTree = async () => {
  if (!projectId.value) return
  loadingTree.value = true
  try {
    const res = await listDocuments(projectId.value)
    tree.value = res.data.documents ?? []
  } finally {
    loadingTree.value = false
  }
}

// 在树里按 id 查找节点（更新标题/归档时同步本地状态用）。
const findNode = (nodes: TreeNode[], id: number): TreeNode | null => {
  for (const n of nodes) {
    if (n.id === id) return n
    const found = findNode(n.children, id)
    if (found) return found
  }
  return null
}

// ── 编辑器状态 ──────────────────────────────────────────────────────────────
const activeId = ref<number | null>(null)
const activeTitle = ref('')
const content = ref('')
const savedContent = ref('')
const viewMode = ref<'edit' | 'split' | 'preview'>('split')
const saving = ref(false)

const dirty = computed(() => content.value !== savedContent.value)
const statusText = computed(() => (saving.value ? '保存中…' : dirty.value ? '未保存' : '已保存'))
const statusClass = computed(() => (dirty.value && !saving.value ? 'pv-editor__status--dirty' : ''))

const renderedHtml = computed(() => {
  const raw = marked.parse(content.value || '', { async: false }) as string
  return DOMPurify.sanitize(raw)
})

const openDoc = async (id: number) => {
  if (id === activeId.value) return
  // 切换文档前若有未保存改动，先冲一次。
  if (dirty.value && activeId.value) await save()
  const res = await getDocument(id)
  activeId.value = id
  activeTitle.value = res.data.document.title
  content.value = res.data.content ?? ''
  savedContent.value = content.value
}

// ── 防抖自动保存 ────────────────────────────────────────────────────────────
let autosaveTimer: ReturnType<typeof setTimeout> | null = null
const onInput = () => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => {
    if (dirty.value) void save()
  }, 1200)
}

const save = async () => {
  if (!activeId.value || saving.value || !dirty.value) return
  const id = activeId.value
  const snapshot = content.value
  saving.value = true
  try {
    await saveDocument(id, snapshot)
    // 仅当期间未再改动才把已保存基线推进到本次快照。
    if (activeId.value === id) savedContent.value = snapshot
  } finally {
    saving.value = false
  }
}

// ── 树操作 ──────────────────────────────────────────────────────────────────
const createDoc = async (parentId: number | null) => {
  if (!projectId.value) return
  const res = await createDocument(projectId.value, { title: '无标题文档', parentId: parentId ?? undefined })
  await loadTree()
  await openDoc(res.data.document.id)
}

// 重命名
const renameOpen = ref(false)
const renameTitle = ref('')
const renameTarget = ref<TreeNode | null>(null)
const startRename = (node: TreeNode) => {
  renameTarget.value = node
  renameTitle.value = node.title
  renameOpen.value = true
}
const applyRename = async () => {
  const node = renameTarget.value
  const title = renameTitle.value.trim()
  if (!node || !title) return
  renameOpen.value = false
  await patchDocument(node.id, { title })
  const local = findNode(tree.value, node.id)
  if (local) local.title = title
  if (activeId.value === node.id) activeTitle.value = title
}

// 归档切换
const toggleArchive = async (node: TreeNode) => {
  const next = !node.archived
  await patchDocument(node.id, { archived: next })
  const local = findNode(tree.value, node.id)
  if (local) local.archived = next
}

// 删除
const deleteOpen = ref(false)
const deleteTarget = ref<TreeNode | null>(null)
const confirmDelete = (node: TreeNode) => {
  deleteTarget.value = node
  deleteOpen.value = true
}
const applyDelete = async () => {
  const node = deleteTarget.value
  if (!node) return
  deleteOpen.value = false
  await deleteDocument(node.id)
  if (activeId.value === node.id) {
    activeId.value = null
    content.value = ''
    savedContent.value = ''
  }
  await loadTree()
}

// ── 生命周期 ────────────────────────────────────────────────────────────────
watch(
  projectId,
  (id) => {
    activeId.value = null
    content.value = ''
    savedContent.value = ''
    if (id) {
      currentProjectName.value = projects.value.find((p) => p.id === id)?.name ?? ''
      void loadTree()
      if (!currentProjectName.value) {
        // 直接进入带 projectId 的链接（未经选择器）时补取名字。
        ProjectsApi.detail(id)
          .then((r) => (currentProjectName.value = r.data.project.name))
          .catch(() => {})
      }
    } else {
      void loadProjects()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
})
</script>

<style scoped>
.pv-shell {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}

/* 项目选择器 */
.pv-picker {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pv-picker__box {
  width: 420px;
  max-width: 90%;
}
.pv-picker__title {
  display: flex;
  align-items: center;
  font-weight: 700;
  font-size: 18px;
  margin-bottom: 12px;
}
.pv-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  padding: 24px 0;
}
.pv-empty__ic {
  color: rgba(var(--v-theme-primary), 0.7);
  margin-bottom: 4px;
}
.pv-empty__title {
  font-weight: 700;
  font-size: 18px;
}
.pv-empty__sub {
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  margin-bottom: 12px;
}
.pv-err {
  color: rgb(var(--v-theme-error));
  font-size: 13px;
  margin-top: 10px;
}
.pv-picker__hint {
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  padding: 8px 0;
}

/* 文档树 */
.pv-tree {
  width: 280px;
  flex: none;
  border-right: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  display: flex;
  flex-direction: column;
}
.pv-tree__hd {
  display: flex;
  align-items: center;
  padding: 8px 8px 8px 8px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pv-tree__scroll {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}
.pv-tree__hint {
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  font-size: 13px;
  padding: 8px 10px;
}
.pv-tree__ft {
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding: 2px 12px;
}

/* 编辑器 */
.pv-editor {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.pv-editor__hd {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pv-editor__title {
  font-weight: 700;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40%;
}
.pv-editor__status {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
}
.pv-editor__status--dirty {
  color: rgb(var(--v-theme-warning));
}
.pv-editor__body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.pv-editor__ta {
  flex: 1;
  min-width: 0;
  resize: none;
  border: none;
  outline: none;
  padding: 16px 20px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 14px;
  line-height: 1.7;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
}
.pv-editor__ta--split {
  border-right: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pv-editor__preview {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 16px 24px;
}
.pv-editor__blank {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
