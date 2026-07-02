<script setup lang="ts">
import { computed, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { renderMarkdown as renderMarkdownWith } from './lib/renderMessage'
import { NOTIF_KIND, label } from './labels'
import {
  getNotifications,
  ingestActivity,
  listProjects,
  listUsers,
  markNotificationRead,
  resolveNotification,
  sendNotificationFeedback,
} from './api'
import type { Me, Notification, Project } from './types'
import CheeseAvatar from './components/CheeseAvatar.vue'
import { me, signIn, signOut } from './me'

// 极简登录 (Phase 0): the main UI only mounts when signed in, so every
// component can read its author handle once at setup.
const loginHandle = ref('')
const loginName = ref('')
const loginBusy = ref(false)
const loginError = ref<string | null>(null)
const knownUsers = ref<Me[]>([])

async function loadKnownUsers() {
  try {
    knownUsers.value = (await listUsers()).data
  } catch {
    knownUsers.value = []
  }
}

async function doSignIn(handle?: string) {
  const h = (handle ?? loginHandle.value).trim().toLowerCase()
  if (!h) return
  loginBusy.value = true
  loginError.value = null
  try {
    await signIn(h, loginName.value)
  } catch (e) {
    loginError.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loginBusy.value = false
  }
}

function doSignOut() {
  signOut()
  loadKnownUsers() // repopulate the quick-pick list for the login screen
}

// Notification bodies are AI/human-authored markdown (e.g. a 子话题 conclusion
// with bullets/bold flowing back via C4), so render them as markdown like the
// chat and doc do — not raw text. Reference tokens (<@handle>, <#topicId>) render
// as chips instead of leaking as literal angle-bracket text. No roster/topic
// maps here, so chips fall back to the handle / "话题" label.
const EMPTY_MAPS = { mentionNames: {}, topicTitles: {} }
function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, EMPTY_MAPS)
}

const route = useRoute()
const router = useRouter()

const projects = ref<Project[]>([])

// The project currently in context, taken from the route param when present.
const currentProjectId = computed<string | null>(() => {
  const p = route.params.projectId
  return typeof p === 'string' && p ? p : null
})

async function loadProjects() {
  try {
    projects.value = (await listProjects()).data
  } catch {
    // Non-fatal; the picker just stays empty.
  }
}
onMounted(() => {
  loadProjects()
  if (!me.value) loadKnownUsers()
})

// If the routed project isn't in the list (created after load / stale tab),
// refetch — otherwise the picker shows the raw id instead of the name.
watch(
  currentProjectId,
  (id) => {
    if (id && !projects.value.some((p) => p.id === id)) loadProjects()
  },
  { immediate: true },
)

function onPickProject(id: string | null) {
  if (!id) return
  // Stay on the same kind of page (工作台 vs 总览) when switching projects.
  const name = route.name === 'overview' ? 'overview' : 'workspace-project'
  router.push({ name, params: { projectId: id } })
}

// Which top-level nav tab is active.
const activeTab = computed<string>(() => {
  if (route.name === 'overview') return 'overview'
  if (route.name === 'calendar') return 'calendar'
  if (route.name === 'project-settings') return 'settings'
  if (route.name === 'market') return 'market'
  if (route.name === 'spaces' || route.name === 'space-board') return 'spaces'
  return 'workspace'
})

function goWorkspace() {
  if (currentProjectId.value) {
    router.push({
      name: 'workspace-project',
      params: { projectId: currentProjectId.value },
    })
  } else {
    router.push({ name: 'workspace' })
  }
}
function goOverview() {
  if (currentProjectId.value) {
    router.push({ name: 'overview', params: { projectId: currentProjectId.value } })
  }
}
function goSpaces() {
  router.push({ name: 'spaces' })
}
function goCalendar() {
  if (currentProjectId.value) {
    router.push({ name: 'calendar', params: { projectId: currentProjectId.value } })
  }
}
function goSettings() {
  if (currentProjectId.value) {
    router.push({
      name: 'project-settings',
      params: { projectId: currentProjectId.value },
    })
  }
}
function goMarket() {
  router.push({ name: 'market' })
}

// ---- 通知中心 (G2/G3): app-bar bell + menu ----
const notifMenu = ref(false)
const notifications = ref<Notification[]>([])
const notifLoading = ref(false)
// silent notifications are recorded but never interrupt — keep them out of the
// bell list and the unread badge (spec §8.5 分级打扰).
const visibleNotifs = computed<Notification[]>(() =>
  notifications.value.filter((n) => n.level !== 'silent'),
)
const unreadCount = computed<number>(
  () => visibleNotifs.value.filter((n) => n.read_at === null).length,
)

async function loadNotifications() {
  if (!currentProjectId.value) {
    notifications.value = []
    return
  }
  notifLoading.value = true
  try {
    const payload = await getNotifications(
      currentProjectId.value,
      me.value?.handle ?? '',
    )
    notifications.value = payload.data
  } catch {
    // Non-fatal; the bell just shows nothing.
  } finally {
    notifLoading.value = false
  }
}

async function onMarkNotifRead(n: Notification) {
  try {
    const updated = await markNotificationRead(n.id)
    n.read_at = updated.read_at
  } catch {
    // ignore
  }
}

async function onNotifFeedback(n: Notification, feedback: 'up' | 'down') {
  try {
    const updated = await sendNotificationFeedback(n.id, feedback)
    n.feedback = updated.feedback
  } catch {
    // ignore
  }
}

// 决策选项 (spec G2): a decision request carries options the user can pick.
function notifOptions(n: Notification): string[] {
  const opts = (n.payload as { options?: unknown })?.options
  return Array.isArray(opts) ? (opts as string[]) : []
}
function notifChoice(n: Notification): string | null {
  const c = (n.payload as { resolved_choice?: unknown })?.resolved_choice
  return typeof c === 'string' ? c : null
}
async function onResolveNotif(n: Notification, chosen: string) {
  try {
    const updated = await resolveNotification(n.id, chosen)
    n.payload = updated.payload
    n.read_at = updated.read_at
  } catch {
    // ignore — the menu stays open for a retry
  }
}

// Real action button label for a notification (the platform renders this — 芝士
// should NOT type fake "[看活文档]/[采纳]" into the body).
function notifActionLabel(n: Notification): string | null {
  if (!n.topic_id) return null
  if (n.kind === 'accept_request') return '去验收'
  return '打开话题'
}
function openNotifTopic(n: Notification) {
  if (!n.topic_id) return
  notifMenu.value = false
  router.push({
    name: 'workspace-project',
    params: { projectId: n.project_id },
    query: { topic: n.topic_id },
  })
  if (n.read_at === null) onMarkNotifRead(n)
}

// Refresh notifications when the project changes or the menu opens.
watch(currentProjectId, () => loadNotifications())
watch(notifMenu, (open) => {
  if (open) loadNotifications()
})

// ---- 记一笔 / 导入 (E1/E3): app-bar dialog ----
const noteDialog = ref(false)
const noteText = ref('')
const noteSubmitting = ref(false)
const toast = ref<string | null>(null)
const toastVisible = computed<boolean>({
  get: () => toast.value !== null,
  set: (v) => {
    if (!v) toast.value = null
  },
})

async function submitNote() {
  const pid = currentProjectId.value
  const text = noteText.value.trim()
  if (!pid || !text) return
  noteSubmitting.value = true
  try {
    await ingestActivity(pid, text, me.value?.handle ?? '')
    noteDialog.value = false
    noteText.value = ''
    toast.value = '芝士已整理成活动话题'
    // Nudge the workspace to refresh its topic tree so the new [活动] topic shows.
    activityBump.value += 1
  } catch (e) {
    toast.value = e instanceof Error ? e.message : '记一笔失败'
  } finally {
    noteSubmitting.value = false
  }
}

// Bumped after 记一笔 so the workspace can pick up the new topic. Provided to
// WorkspaceView (which injects + watches it) so it re-fetches topics without a
// full remount (chat/doc state stays intact).
const activityBump = ref(0)
provide('activityBump', activityBump)
</script>

<template>
  <v-app>
    <v-app-bar flat density="compact" color="surface" border="b" class="top-nav">
      <!-- Brand — the brand mark keeps amber on the word; mark is ink. -->
      <div class="d-flex align-center ga-2 ps-4 pe-2">
        <CheeseAvatar :size="26" />
        <span class="brand-word">知是</span>
        <span class="brand-tag d-none d-sm-inline">CheeseX</span>
      </div>

      <!-- Nav tabs: active = --ink + 2px amber underline; inactive = --muted -->
      <v-tabs
        :model-value="activeTab"
        color="primary"
        density="compact"
        class="ms-5 nav-tabs"
        slider-color="primary"
      >
        <v-tab value="workspace" @click="goWorkspace">工作台</v-tab>
        <v-tab
          value="overview"
          :disabled="!currentProjectId"
          @click="goOverview"
        >
          项目总览
        </v-tab>
        <v-tab
          value="calendar"
          :disabled="!currentProjectId"
          @click="goCalendar"
        >
          日历
        </v-tab>
        <v-tab value="spaces" @click="goSpaces">机构看板</v-tab>
        <v-tab value="market" @click="goMarket">市场</v-tab>
      </v-tabs>

      <v-spacer />

      <!-- Project picker — only where there's no rail (workspace switches the
           project from the rail's 本体 header instead). -->
      <v-select
        v-if="activeTab !== 'workspace'"
        :model-value="currentProjectId"
        :items="projects"
        item-title="name"
        item-value="id"
        placeholder="选择项目…"
        density="compact"
        variant="outlined"
        hide-details
        prepend-inner-icon="mdi-folder-outline"
        class="project-picker me-3"
        style="max-width: 220px"
        @update:model-value="onPickProject"
      />

      <!-- 项目设置 (资源池): a gear, not a primary tab — it's project config. -->
      <v-btn
        icon
        variant="text"
        size="small"
        class="me-1"
        :disabled="!currentProjectId"
        :color="activeTab === 'settings' ? 'primary' : undefined"
        title="项目设置（资源池）"
        @click="goSettings"
      >
        <v-icon>mdi-cog-outline</v-icon>
      </v-btn>

      <!-- 记一笔 / 导入 (E1/E3) -->
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-pencil-plus"
        class="me-1"
        :disabled="!currentProjectId"
        @click="noteDialog = true"
      >
        记一笔
      </v-btn>

      <!-- 通知中心 (G2/G3): bell + unread badge -->
      <v-menu
        v-model="notifMenu"
        :close-on-content-click="false"
        location="bottom end"
        offset="8"
      >
        <template #activator="{ props: menuProps }">
          <v-btn
            icon
            variant="text"
            size="small"
            class="me-2"
            :disabled="!currentProjectId"
            v-bind="menuProps"
          >
            <v-badge
              :model-value="unreadCount > 0"
              :content="unreadCount"
              color="error"
            >
              <v-icon>mdi-bell-outline</v-icon>
            </v-badge>
          </v-btn>
        </template>

        <v-card width="380" max-height="520" class="d-flex flex-column">
          <v-toolbar density="comfortable" flat color="surface" border="b">
            <v-toolbar-title class="t-title">
              <v-icon size="17" class="me-1 c-faint">mdi-bell-outline</v-icon>
              通知
            </v-toolbar-title>
            <span v-if="unreadCount" class="chip-neutral me-3">
              {{ unreadCount }} 未读
            </span>
          </v-toolbar>

          <div class="overflow-y-auto">
            <div
              v-if="notifLoading"
              class="d-flex justify-center py-6"
            >
              <v-progress-circular indeterminate color="primary" size="24" />
            </div>
            <div
              v-else-if="visibleNotifs.length === 0"
              class="text-center text-medium-emphasis py-8"
            >
              暂无通知
            </div>
            <div v-else class="pa-2 d-flex flex-column ga-2">
              <div
                v-for="n in visibleNotifs"
                :key="n.id"
                class="notif-item"
                :class="{
                  'notif-read': n.read_at !== null,
                  'notif-strong': n.level === 'strong',
                }"
              >
                <div class="d-flex align-center ga-2 mb-1">
                  <span class="chip-neutral">{{ label(NOTIF_KIND, n.kind) }}</span>
                  <span class="t-title">{{ n.title }}</span>
                  <span
                    v-if="n.target_handle"
                    class="t-meta"
                    style="font-family: var(--font-mono)"
                  >@{{ n.target_handle }}</span>
                </div>
                <div
                  v-if="n.body"
                  class="t-body md-content c-muted mb-2"
                  v-html="renderMarkdown(n.body)"
                />
                <!-- 拍板 (spec G2): pick an option to resolve a decision request -->
                <div
                  v-if="notifOptions(n).length"
                  class="d-flex flex-wrap ga-1 mb-2"
                >
                  <template v-if="notifChoice(n)">
                    <span class="t-meta">
                      已选择：<strong style="color: var(--ink)">{{ notifChoice(n) }}</strong>
                    </span>
                  </template>
                  <template v-else>
                    <v-btn
                      v-for="opt in notifOptions(n)"
                      :key="opt"
                      size="x-small"
                      variant="outlined"
                      class="btn-secondary"
                      @click="onResolveNotif(n, opt)"
                    >
                      {{ opt }}
                    </v-btn>
                  </template>
                </div>
                <!-- Real action (platform-rendered, not 芝士's typed text) -->
                <div v-if="notifActionLabel(n)" class="mb-2">
                  <v-btn
                    size="x-small"
                    variant="flat"
                    color="primary"
                    @click="openNotifTopic(n)"
                  >
                    {{ notifActionLabel(n) }}
                  </v-btn>
                </div>
                <div class="d-flex align-center ga-1">
                  <v-btn
                    :icon="n.feedback === 'up' ? 'mdi-thumb-up' : 'mdi-thumb-up-outline'"
                    size="x-small"
                    variant="text"
                    :color="n.feedback === 'up' ? 'primary' : undefined"
                    @click="onNotifFeedback(n, 'up')"
                  />
                  <v-btn
                    :icon="n.feedback === 'down' ? 'mdi-thumb-down' : 'mdi-thumb-down-outline'"
                    size="x-small"
                    variant="text"
                    :color="n.feedback === 'down' ? 'primary' : undefined"
                    @click="onNotifFeedback(n, 'down')"
                  />
                  <v-spacer />
                  <v-btn
                    v-if="n.read_at === null"
                    size="x-small"
                    variant="text"
                    @click="onMarkNotifRead(n)"
                  >
                    标记已读
                  </v-btn>
                  <span v-else class="d-inline-flex align-center ga-1 c-faint" style="font-size: 12px">
                    <span class="status-dot status-dot--ok" />已读
                  </span>
                </div>
              </div>
            </div>
          </div>
        </v-card>
      </v-menu>

      <!-- User chip — neutral (--fill), not amber. Click → switch identity. -->
      <v-menu v-if="me" location="bottom end">
        <template #activator="{ props: chip }">
          <div class="user-chip me-4" v-bind="chip" style="cursor: pointer">
            <div class="user-chip__avatar">
              {{ me.name.slice(0, 1).toUpperCase() }}
            </div>
            <span class="user-chip__name">{{ me.name }}</span>
          </div>
        </template>
        <v-list density="compact" min-width="180">
          <v-list-item disabled>
            <v-list-item-title class="t-meta">@{{ me.handle }}</v-list-item-title>
          </v-list-item>
          <v-list-item prepend-icon="mdi-logout" @click="doSignOut">
            <v-list-item-title>退出登录</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
    </v-app-bar>

    <!-- 记一笔 / 导入 (E1/E3): 芝士 digests raw input into an [活动] topic. -->
    <v-dialog v-model="noteDialog" max-width="560">
      <v-card rounded="lg">
        <v-card-title class="d-flex align-center ga-2 t-title pt-4">
          <v-icon size="19" class="c-muted">mdi-pencil-plus</v-icon>
          记一笔
        </v-card-title>
        <v-card-text>
          <v-textarea
            v-model="noteText"
            variant="outlined"
            rows="6"
            auto-grow
            hide-details
            placeholder="说一句今天做了什么，或粘贴会议纪要/聊天记录"
            :disabled="noteSubmitting"
          />
          <div class="t-meta mt-2" style="line-height: 1.5">
            芝士会把它整理成一个结构化的活动话题（可能需要几秒）。
          </div>
        </v-card-text>
        <v-card-actions class="px-4 pb-4">
          <v-spacer />
          <v-btn
            variant="text"
            class="c-muted"
            :disabled="noteSubmitting"
            @click="noteDialog = false"
          >
            取消
          </v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="noteSubmitting"
            :disabled="!noteText.trim()"
            @click="submitNote"
          >
            交给芝士
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="toastVisible" timeout="3500" location="bottom">
      {{ toast }}
    </v-snackbar>

    <v-main class="app-main">
      <!-- 登录门 (Phase 0): the workspace only mounts when signed in, so every
           component reads a real author handle at setup. -->
      <router-view v-if="me" />
      <div v-else class="login-gate fill-height d-flex align-center justify-center">
        <v-card rounded="lg" width="380" class="pa-6" elevation="2">
          <div class="d-flex align-center ga-2 mb-1">
            <CheeseAvatar :size="24" />
            <span class="t-title">进入知是</span>
          </div>
          <p class="t-meta c-muted mb-4">
            报上名号即可——一个 handle 就是你的身份。
          </p>
          <div v-if="knownUsers.length" class="mb-4">
            <div class="t-meta c-muted mb-2">已有成员，点击直接进入：</div>
            <div class="d-flex flex-wrap ga-2">
              <v-chip
                v-for="u in knownUsers"
                :key="u.handle"
                size="small"
                :disabled="loginBusy"
                @click="doSignIn(u.handle)"
              >
                {{ u.name }}（@{{ u.handle }}）
              </v-chip>
            </div>
            <v-divider class="my-4" />
          </div>
          <v-text-field
            v-model="loginName"
            label="名字"
            density="compact"
            variant="outlined"
            hide-details
            class="mb-3"
          />
          <v-text-field
            v-model="loginHandle"
            label="handle（小写字母/数字/横线）"
            density="compact"
            variant="outlined"
            hide-details
            class="mb-4"
            @keydown.enter="doSignIn()"
          />
          <v-alert
            v-if="loginError"
            type="error"
            density="compact"
            class="mb-3"
            :text="loginError"
          />
          <v-btn
            color="primary"
            variant="flat"
            block
            :loading="loginBusy"
            :disabled="!loginHandle.trim()"
            @click="doSignIn()"
          >
            进入
          </v-btn>
        </v-card>
      </div>
    </v-main>
  </v-app>
</template>

<style scoped>
/* Brand mark — the one place the amber word is allowed. */
.brand-word {
  font-family: var(--font-display);
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--accent);
}
.brand-tag {
  font-family: var(--font-mono);
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: var(--faint);
}

/* Top nav — neutral surface, hairline bottom border. */
.top-nav {
  border-bottom: 1px solid var(--line);
}
/* Tabs: inactive --muted, active --ink; the 2px amber slider is the only accent.
   Disable the default ripple-tint so amber doesn't bleed onto the label bg. */
.nav-tabs :deep(.v-tab) {
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  min-width: 0;
}
.nav-tabs :deep(.v-tab.v-tab--selected) {
  color: var(--ink);
  font-weight: 600;
}
.nav-tabs :deep(.v-tab .v-btn__overlay) {
  opacity: 0 !important;
}

/* User chip — neutral. */
.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 10px 0 6px;
  background: var(--fill);
  border-radius: 8px;
}
.user-chip__avatar {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  background: var(--fill-2);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.user-chip__name {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--muted);
}

/* Notification item — flat inset, no card chrome. */
.notif-item {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--fill);
  border-left: 2px solid transparent;
}
/* strong = needs attention: a quiet amber accent (light/silent stay neutral). */
.notif-strong {
  border-left-color: var(--accent);
  background: var(--surface);
}
.notif-read {
  opacity: 0.55;
}
/* Markdown body in a notification: compact margins so it reads as one tidy
   block inside the menu, not a full document. */
.t-body.md-content :deep(p) {
  margin: 0 0 0.35em;
}
.t-body.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.t-body.md-content :deep(ul),
.t-body.md-content :deep(ol) {
  margin: 0.2em 0;
  padding-left: 1.2em;
}
.t-body.md-content :deep(li) {
  margin: 0.1em 0;
}
.t-body.md-content :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.9em;
  background: var(--fill);
  padding: 0 3px;
  border-radius: 3px;
}
.t-body.md-content :deep(h1),
.t-body.md-content :deep(h2),
.t-body.md-content :deep(h3) {
  font-size: 1em;
  font-weight: 600;
  margin: 0.3em 0 0.15em;
}
.t-body.md-content :deep(blockquote) {
  margin: 0.25em 0;
  padding-left: 8px;
  border-left: 2px solid rgba(var(--v-border-color), 0.5);
  color: var(--muted);
}
/* Reference chips (<@handle> / <#topicId>) in a notification body. */
.t-body.md-content :deep(.mention) {
  padding: 0 4px;
  border-radius: 4px;
  font-weight: 500;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
  white-space: nowrap;
}

/* v-main fills the viewport below the app bar; pages own their own scroll. */
.app-main {
  height: 100vh;
}
:deep(.v-main__wrap),
:deep(.v-main) {
  min-height: 0;
}
.project-picker :deep(.v-field) {
  font-size: 0.85rem;
}
</style>
