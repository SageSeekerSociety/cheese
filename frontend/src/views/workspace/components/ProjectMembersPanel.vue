<!-- 项目成员管理侧栏（飞书式）：从项目工作区右上角「成员」拉出、贴右侧滑入。
     一个统一列表同时列出人类与项目 agent（都是成员；agent 带机器人徽标、可点开现场）。
     忽略权限：任何成员都能添加/移除成员、创建/停止项目 agent（扁平、无角色区分）。
     搜索用户加入；对在线 agent 提供「停止」；底部一键创建项目 agent。 -->
<template>
  <teleport to="body">
    <transition name="pmp-fade">
      <div v-if="open" class="pmp-backdrop" @click="close" />
    </transition>
    <transition name="pmp-slide">
      <aside v-if="open" class="pmp">
        <header class="pmp-head">
          <span class="pmp-head__title">项目成员</span>
          <v-spacer />
          <v-btn icon="mdi-close" size="small" variant="text" @click="close" />
        </header>

        <div class="pmp-body">
          <section class="pmp-sec">
            <div class="pmp-sec__title">
              成员 <span class="pmp-count">{{ members.length }}</span>
              <v-spacer />
              <v-btn size="small" variant="text" class="mr-1" @click="manageMode = !manageMode">
                {{ manageMode ? '完成' : '管理' }}
              </v-btn>
              <v-menu v-model="addMenu" :close-on-content-click="false" location="bottom end" :z-index="2600">
                <template #activator="{ props: mp }">
                  <v-btn v-bind="mp" size="small" color="primary" variant="tonal">＋ 添加成员</v-btn>
                </template>
                <v-card min-width="260" class="pmp-add">
                  <v-text-field
                    v-model="candQuery"
                    placeholder="搜索用户名或昵称…"
                    density="compact"
                    variant="solo-filled"
                    flat
                    hide-details
                    prepend-inner-icon="mdi-magnify"
                    autofocus
                  />
                  <div class="pmp-cand-list">
                    <div
                      v-for="c in candidates"
                      :key="c.user_id"
                      class="pmp-row pmp-row--pick"
                      @click="add(c)"
                    >
                      <UserAvatar :user-id="c.user_id" :avatar-id="c.avatar_id" :nickname="c.nickname" :size="30" :clickable="false" />
                      <span class="pmp-name">{{ c.nickname }}</span>
                      <v-spacer />
                      <v-progress-circular v-if="adding === c.user_id" size="16" width="2" indeterminate />
                      <v-icon v-else icon="mdi-plus" size="small" />
                    </div>
                    <div v-if="!candidates.length" class="pmp-empty">
                      {{ candQuery.trim() ? '无匹配用户' : '输入关键字搜索用户' }}
                    </div>
                  </div>
                </v-card>
              </v-menu>
            </div>
            <div v-if="error" class="pmp-err">{{ error }}</div>

            <div v-for="m in members" :key="m.user_id" class="pmp-row pmp-row--member">
              <UserAvatar :user-id="m.user_id" :avatar-id="m.avatar_id" :nickname="m.nickname" :size="32" />
              <div class="pmp-meta">
                <span class="pmp-name">{{ m.nickname }}</span>
              </div>
              <v-spacer />
              <template v-if="manageMode">
                <!-- 在线 agent：重启并恢复会话 / 停止（软删）；否则：移除成员 -->
                <v-btn
                  v-if="m.is_agent && m.sid && m.device_id"
                  size="x-small"
                  variant="text"
                  :loading="busy === m.user_id"
                  title="用同一个 Claude 会话重启该 agent（复用其用户与历史）"
                  @click="recreateAgent(m)"
                >
                  重启并恢复
                </v-btn>
                <v-btn
                  v-if="m.is_agent && m.sid && m.device_id"
                  size="x-small"
                  variant="text"
                  color="error"
                  :loading="busy === m.user_id"
                  @click="stopAgent(m)"
                >
                  停止 agent
                </v-btn>
                <v-btn
                  v-else-if="m.user_id !== selfId"
                  size="x-small"
                  variant="text"
                  color="error"
                  :loading="busy === m.user_id"
                  @click="remove(m)"
                >
                  移除
                </v-btn>
              </template>
              <div v-else class="pmp-tags">
                <span v-if="m.is_agent" class="pmp-tag pmp-tag--agent">agent</span>
                <span v-else-if="m.role === 2" class="pmp-tag">负责人</span>
              </div>
            </div>
            <div v-if="!members.length && !loading" class="pmp-empty">暂无成员</div>
          </section>
        </div>

        <div class="pmp-foot">
          <v-btn
            block
            variant="tonal"
            size="small"
            color="primary"
            prepend-icon="mdi-robot-happy-outline"
            :loading="creatingAgent"
            @click="createAgent"
          >
            在项目里创建 agent
          </v-btn>
          <div v-if="agentError" class="pmp-err mt-1">{{ agentError }}</div>
        </div>
      </aside>
    </transition>
  </teleport>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'

import UserAvatar from '@/components/common/UserAvatar.vue'
import type { Member } from '@/network/api/projectMembers'
import {
  addProjectMember,
  createProjectAgent,
  deleteProjectAgent,
  listProjectMembers,
  recreateProjectAgent,
  removeProjectMember,
  searchMemberCandidates,
} from '@/network/api/projectMembers'
import { currentUserId } from '@/services/account'

const props = defineProps<{ open: boolean; projectId: number | null }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const members = ref<Member[]>([])
const loading = ref(false)
const error = ref('')
const manageMode = ref(false)
const busy = ref<number | null>(null)
const selfId = currentUserId

function close(): void {
  emit('update:open', false)
}

async function refresh(): Promise<void> {
  if (!props.projectId) return
  loading.value = true
  try {
    const { members: fresh } = await listProjectMembers(props.projectId)
    members.value = fresh
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载成员失败'
  } finally {
    loading.value = false
  }
}

// ── 添加成员 ──
const addMenu = ref(false)
const candQuery = ref('')
const candidates = ref<Member[]>([])
const adding = ref<number | null>(null)
let candTimer = 0

async function loadCandidates(): Promise<void> {
  if (!props.projectId || !candQuery.value.trim()) {
    candidates.value = []
    return
  }
  try {
    const { candidates: fresh } = await searchMemberCandidates(props.projectId, candQuery.value.trim())
    candidates.value = fresh
  } catch {
    candidates.value = []
  }
}
watch(candQuery, () => {
  clearTimeout(candTimer)
  candTimer = window.setTimeout(loadCandidates, 250)
})
watch(addMenu, (o) => {
  if (o) {
    candQuery.value = ''
    candidates.value = []
  }
})

async function add(c: Member): Promise<void> {
  if (!props.projectId) return
  adding.value = c.user_id
  error.value = ''
  try {
    await addProjectMember(props.projectId, c.user_id)
    await refresh()
    void loadCandidates()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '添加失败'
  } finally {
    adding.value = null
  }
}

// ── 移除成员 / 停止 agent ──
async function remove(m: Member): Promise<void> {
  if (!props.projectId) return
  busy.value = m.user_id
  error.value = ''
  try {
    await removeProjectMember(props.projectId, m.user_id)
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '移除失败'
  } finally {
    busy.value = null
  }
}

async function stopAgent(m: Member): Promise<void> {
  if (!m.sid || !m.device_id) return
  busy.value = m.user_id
  error.value = ''
  try {
    await deleteProjectAgent(m.device_id, m.sid)
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '停止 agent 失败'
  } finally {
    busy.value = null
  }
}

// ── 重启并恢复会话（复用原用户 + 原 Claude session）──
async function recreateAgent(m: Member): Promise<void> {
  if (!props.projectId) return
  if (!window.confirm(`将结束 ${m.nickname} 当前的现场，并用同一个 Claude 会话重启它。继续？`)) return
  busy.value = m.user_id
  error.value = ''
  try {
    // 现场仍存活，必须 force；resume 沿用原会话继续对话。
    await recreateProjectAgent(props.projectId, m.user_id, { resume: true, force: true })
    await refresh()
    setTimeout(refresh, 3000)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重启失败'
  } finally {
    busy.value = null
  }
}

// ── 创建项目 agent ──
const creatingAgent = ref(false)
const agentError = ref('')
async function createAgent(): Promise<void> {
  if (!props.projectId) return
  creatingAgent.value = true
  agentError.value = ''
  try {
    await createProjectAgent(props.projectId)
    // agent 上线到列表需几秒（现场就绪后才出现在成员的 is_agent），稍后刷新。
    await refresh()
    setTimeout(refresh, 3000)
  } catch (e) {
    agentError.value = e instanceof Error ? e.message : '创建失败——是否有客户机接入本项目？'
  } finally {
    creatingAgent.value = false
  }
}

// ── 生命周期：打开时刷新，并轮询保持 agent 在线状态新鲜 ──
let timer = 0
watch(
  () => [props.open, props.projectId] as const,
  ([o]) => {
    if (o) {
      manageMode.value = false
      void refresh()
    }
  },
  { immediate: true }
)
timer = window.setInterval(() => {
  if (props.open) void refresh()
}, 5000)
onUnmounted(() => {
  clearInterval(timer)
  clearTimeout(candTimer)
})
</script>

<style scoped>
.pmp-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 2390;
}
.pmp {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  width: 340px;
  max-width: 92vw;
  z-index: 2400;
  background: rgb(var(--v-theme-surface));
  border-left: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  box-shadow: -8px 0 28px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
}
.pmp-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 12px 8px 12px 16px;
  font-weight: 700;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pmp-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
}
.pmp-sec {
  margin-bottom: 18px;
}
.pmp-sec__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 6px 0;
}
.pmp-count {
  opacity: 0.5;
  font-weight: 400;
}
.pmp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 4px;
  border-radius: 6px;
}
.pmp-row--member:hover,
.pmp-row--pick:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.pmp-row--pick {
  cursor: pointer;
}
.pmp-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.pmp-name {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pmp-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
  flex: none;
}
.pmp-tag {
  font-size: 11px;
  background: rgba(var(--v-theme-primary), 0.15);
  color: rgb(var(--v-theme-primary));
  padding: 1px 6px;
  border-radius: 4px;
  flex: none;
}
.pmp-tag--agent {
  background: rgba(var(--v-theme-secondary), 0.16);
  color: rgb(var(--v-theme-secondary));
}
.pmp-foot {
  flex: none;
  padding: 10px 12px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.pmp-empty {
  font-size: 12px;
  opacity: 0.5;
  padding: 6px 4px;
}
.pmp-err {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  padding: 4px;
}
.pmp-add {
  padding: 8px;
}
.pmp-cand-list {
  max-height: 300px;
  overflow-y: auto;
  margin-top: 6px;
}
.pmp-slide-enter-active,
.pmp-slide-leave-active {
  transition: transform 0.22s ease;
}
.pmp-slide-enter-from,
.pmp-slide-leave-to {
  transform: translateX(100%);
}
.pmp-fade-enter-active,
.pmp-fade-leave-active {
  transition: opacity 0.22s ease;
}
.pmp-fade-enter-from,
.pmp-fade-leave-to {
  opacity: 0;
}
</style>
