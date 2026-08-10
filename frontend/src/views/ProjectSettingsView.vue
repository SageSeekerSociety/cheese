<script setup lang="ts">
import type {
  ComputeProfiles,
  ExecProfiles,
  ExpertRole,
  GithubConnection,
  OAuthConnectionInfo,
  SandboxImageInfo,
  UpstreamSyncResult,
} from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  connectGithubRepo as apiConnectGithubRepo,
  createRole,
  deleteOAuthConnection,
  getExecutionProfiles,
  getGithubAccountAuthorizeUrl,
  getGithubConnection,
  getModelProfiles,
  getProject,
  getSandboxImage,
  getUpstream,
  listOAuthConnections,
  listRoles,
  setExecutionProfile,
  setModelProfile,
  setProjectExpertRole,
  setSandboxImage,
  setUpstream,
  syncUpstream,
} from '../api'
import { findGithubAccountConnection, isGithubAccountTokenExpired } from '../lib/githubAccount'
import { relTime } from '../lib/relTime'
import { me, myHandle } from '../me'

// Project settings. Compute is intentionally absent: execution-architecture v4
// places the pool/default on the team and the per-run choice on the topic.
const props = defineProps<{ projectId: string }>()
const router = useRouter()
const route = useRoute()

const projectName = ref('')
const ai = ref<ExecProfiles | null>(null)
const model = ref<ComputeProfiles | null>(null)
const savingModel = ref<string | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const env = ref<SandboxImageInfo | null>(null)
const savingAi = ref<string | null>(null)
// '' is the sentinel for the default-image row (image null); a real image key otherwise.
const savingEnv = ref<string | null>(null)
// 上游仓库: the linked repo URL as edited, plus save/sync state and last result.
const upstreamUrl = ref('')
const upstreamSaved = ref<string | null>(null)
const savingUpstream = ref(false)
const syncing = ref(false)
const syncResult = ref<UpstreamSyncResult | null>(null)

// GitHub App install flow (#192): repo connection is read-only status here —
// connecting/reconnecting happens on github.com, not in this form.
const githubConnection = ref<GithubConnection | null>(null)
const connectingGithubRepo = ref(false)
const connectingGithubAccount = ref(false)
// Set from ?github_install=/&github_account= on the redirect back from our
// own callback routes (app/api/routes/github_install.py, github_account_link.py).
const githubCallbackNotice = ref<{ type: 'success' | 'error' | 'info'; text: string } | null>(null)

// 连接 GitHub 账号 status: loaded independently from `load()` (its own
// loading/error state) so a failure here can't be mistaken for "not
// connected" — see the section's four-branch template below.
type GithubAccountLoadState = 'loading' | 'loaded' | 'error'
const githubAccountLoadState = ref<GithubAccountLoadState>('loading')
const githubAccountLoadError = ref<string | null>(null)
const githubAccountConn = ref<OAuthConnectionInfo | null>(null)
const disconnectingGithubAccount = ref(false)

async function loadGithubAccountConnection() {
  const userId = me.value?.id
  if (!userId) {
    githubAccountLoadState.value = 'error'
    githubAccountLoadError.value = '未登录'
    return
  }
  githubAccountLoadState.value = 'loading'
  githubAccountLoadError.value = null
  try {
    const { connections } = await listOAuthConnections(userId)
    githubAccountConn.value = findGithubAccountConnection(connections)
    githubAccountLoadState.value = 'loaded'
  } catch (e) {
    githubAccountLoadError.value = e instanceof Error ? e.message : '加载连接状态失败'
    githubAccountLoadState.value = 'error'
  }
}

async function disconnectGithubAccount() {
  const userId = me.value?.id
  const conn = githubAccountConn.value
  if (!userId || !conn) return
  disconnectingGithubAccount.value = true
  try {
    await deleteOAuthConnection(userId, conn.id)
    githubAccountConn.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : '断开 GitHub 账号失败'
  } finally {
    disconnectingGithubAccount.value = false
  }
}

// 专家角色 (spec §8.2): which persona 芝士 loads for this project. The catalog
// merges built-in library roles with custom ones; '' = generic 芝士.
const roles = ref<ExpertRole[]>([])
const roleCurrent = ref('')
const savingRole = ref(false)
// 新建角色 dialog: the Claude Code agents-file fields (frontmatter + body).
const roleDialog = ref(false)
const newRoleName = ref('')
const newRoleTitle = ref('')
const newRoleDescription = ref('')
const newRoleBody = ref('')
const creatingRole = ref(false)
const roleFormError = ref<string | null>(null)

const roleItems = computed(() => [
  { name: '', title: '不设置（通用芝士）', description: '' },
  ...roles.value.map((r) => ({
    name: r.name,
    title: r.builtin ? r.title : `${r.title || r.name}（自定义）`,
    description: r.description,
  })),
])

async function pickRole(name: string | null) {
  const next = name ?? ''
  savingRole.value = true
  try {
    const r = await setProjectExpertRole(props.projectId, next)
    roleCurrent.value = r.current ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换专家角色失败'
  } finally {
    savingRole.value = false
  }
}

async function submitNewRole() {
  roleFormError.value = null
  creatingRole.value = true
  try {
    const created = await createRole({
      name: newRoleName.value.trim(),
      title: newRoleTitle.value.trim(),
      description: newRoleDescription.value.trim(),
      body: newRoleBody.value.trim(),
      created_by: myHandle(),
    })
    // A custom role shadows its built-in namesake — keep one entry per name.
    roles.value = [...roles.value.filter((r) => r.name !== created.name), created]
    roleDialog.value = false
    newRoleName.value = ''
    newRoleTitle.value = ''
    newRoleDescription.value = ''
    newRoleBody.value = ''
    // Creating a role from here means "use it": select it right away.
    await pickRole(created.name)
  } catch (e) {
    roleFormError.value = e instanceof Error ? e.message : '创建角色失败'
  } finally {
    creatingRole.value = false
  }
}

const TIER_LABEL: Record<string, string> = {
  default: '默认',
  included: '包含',
  testing: '内测',
  byo: '自带',
  premium: '增值',
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [proj, execP, modelP, envP, upP, rolesP, ghP] = await Promise.all([
      getProject(props.projectId),
      getExecutionProfiles(props.projectId),
      getModelProfiles(props.projectId),
      getSandboxImage(props.projectId),
      getUpstream(props.projectId),
      listRoles(),
      getGithubConnection(props.projectId),
    ])
    projectName.value = proj.name
    ai.value = execP
    model.value = modelP
    env.value = envP
    upstreamSaved.value = upP.url
    upstreamUrl.value = upP.url ?? ''
    roles.value = rolesP.data
    roleCurrent.value = proj.expert_role ?? ''
    githubConnection.value = ghP
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设置失败'
  } finally {
    loading.value = false
  }
}

async function pickAi(name: string) {
  if (!ai.value || ai.value.current === name) return
  savingAi.value = name
  try {
    const r = await setExecutionProfile(props.projectId, name)
    ai.value = { ...ai.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换 AI 池失败'
  } finally {
    savingAi.value = null
  }
}

async function pickModel(id: string) {
  if (!model.value || model.value.current === id) return
  savingModel.value = id
  try {
    const r = await setModelProfile(props.projectId, id)
    model.value = { ...model.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换模型失败'
  } finally {
    savingModel.value = null
  }
}

// Pick the env image. image='' means the default (pool base image → current null).
async function pickEnv(image: string) {
  if (!env.value || (env.value.current ?? '') === image) return
  savingEnv.value = image || '__default__'
  try {
    const r = await setSandboxImage(props.projectId, image)
    env.value = { ...env.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换环境镜像失败'
  } finally {
    savingEnv.value = null
  }
}

// Save (or with an empty field, unlink) the upstream repo URL.
async function saveUpstream() {
  savingUpstream.value = true
  syncResult.value = null
  try {
    const r = await setUpstream(props.projectId, upstreamUrl.value.trim())
    upstreamSaved.value = r.url
    upstreamUrl.value = r.url ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存上游仓库失败'
  } finally {
    savingUpstream.value = false
  }
}

// Pull the upstream's new commits into the project repo (merge; conflicts abort
// cleanly and show up in the result line).
async function doSyncUpstream() {
  syncing.value = true
  syncResult.value = null
  try {
    syncResult.value = await syncUpstream(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '同步上游失败'
  } finally {
    syncing.value = false
  }
}

// Send the browser to GitHub's install page; the callback (github_install.py)
// bounces back here with ?github_install=success|pending|error afterward.
async function connectGithubRepo() {
  connectingGithubRepo.value = true
  try {
    // Try an existing installation first: GitHub's install page dead-ends
    // (never fires the callback) when the App is already installed, so the
    // backend looks for an installation covering the upstream repo itself.
    const res = await apiConnectGithubRepo(props.projectId)
    if (res.connected) {
      githubConnection.value = { connected: true, repo: res.repo, account: res.account }
      githubCallbackNotice.value = { type: 'success', text: `已连接 ${res.repo}` }
      connectingGithubRepo.value = false
      return
    }
    if (res.install_url) {
      window.location.href = res.install_url
      return
    }
    error.value = '连接失败：后端没有返回安装链接'
    connectingGithubRepo.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : '连接 GitHub 仓库失败'
    connectingGithubRepo.value = false
  }
}

// Same shape, but for the App's user-to-server "连接 GitHub 账号" — a
// separate identity link, not a repo connection (github_account_link.py).
async function connectGithubAccount() {
  connectingGithubAccount.value = true
  try {
    const { url } = await getGithubAccountAuthorizeUrl(props.projectId)
    window.location.href = url
  } catch (e) {
    error.value = e instanceof Error ? e.message : '获取授权链接失败'
    connectingGithubAccount.value = false
  }
}

// The two callback routes redirect back to this exact page with a result in
// the query string (no session/cookie hand-off — this page is the only
// signal). Read it once, show it, then strip it so a refresh doesn't repeat it.
function consumeGithubCallbackNotice() {
  const install = route.query.github_install as string | undefined
  const account = route.query.github_account as string | undefined
  if (!install && !account) return

  if (install === 'success') {
    const repo = route.query.repo as string | undefined
    githubCallbackNotice.value = { type: 'success', text: `已连接仓库 ${repo ?? ''}`.trim() }
  } else if (install === 'pending') {
    githubCallbackNotice.value = { type: 'info', text: '安装请求已提交，等待组织管理员批准。' }
  } else if (install === 'error') {
    githubCallbackNotice.value = { type: 'error', text: `连接仓库失败：${route.query.reason ?? '未知原因'}` }
  } else if (account === 'success') {
    githubCallbackNotice.value = { type: 'success', text: '已连接 GitHub 账号。' }
  } else if (account === 'error') {
    githubCallbackNotice.value = { type: 'error', text: `连接账号失败：${route.query.reason ?? '未知原因'}` }
  }

  const { github_install, github_account, repo, reason, ...rest } = route.query
  void github_install
  void github_account
  void repo
  void reason
  router.replace({ query: rest })
}

// Back to wherever you came from (the workspace, via the gear), with an overview
// fallback for a deep link — same pattern as the member page.
function goBack() {
  if (window.history.state?.back != null) router.back()
  else router.push({ name: 'overview', params: { projectId: props.projectId } })
}

onMounted(() => {
  consumeGithubCallbackNotice()
  load()
  loadGithubAccountConnection()
})
watch(() => props.projectId, load)
</script>

<template>
  <div class="settings-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="d-flex align-center mb-4">
        <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" class="px-1" @click="goBack"> 返回 </v-btn>
        <v-spacer />
        <v-btn
          variant="text"
          size="small"
          append-icon="mdi-storefront-outline"
          @click="router.push({ name: 'market' })"
        >
          逛市场
        </v-btn>
      </div>

      <div class="mb-6">
        <div class="t-eyebrow mb-1">项目设置 · {{ projectName }}</div>
        <h1 class="t-page-title">资源池</h1>
        <p class="t-body c-muted mt-1" style="max-width: 640px">
          选择这个项目用哪套 AI 模型、跑在哪套算力上。默认都是知是自己的池，开箱即用； 需要更强的模型或专属机器，可以在
          <a class="link" @click="router.push({ name: 'market' })">市场</a> 里挑。
        </p>
      </div>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
        {{ error }}
      </v-alert>

      <template v-else>
        <!-- 专家角色 (spec §8.2): which persona 芝士 loads for this project -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-account-school-outline</v-icon>
            <span class="ln-section-title">专家角色</span>
          </div>
          <div class="ln-body">
            <div class="d-flex align-center" style="gap: 8px">
              <v-select
                :model-value="roleCurrent"
                :items="roleItems"
                item-title="title"
                item-value="name"
                density="compact"
                variant="outlined"
                hide-details
                :loading="savingRole"
                :disabled="savingRole"
                style="flex: 1"
                @update:model-value="pickRole"
              >
                <template #item="{ props: itemProps, item }">
                  <v-list-item v-bind="itemProps" :subtitle="item.raw.description || undefined" />
                </template>
              </v-select>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" @click="roleDialog = true"> 新建角色 </v-btn>
            </div>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              芝士以这个专家身份进驻项目（影响它的口吻和关注点）。平台内置了几个； 也可以给机构或自己定义新角色。
            </p>
          </div>
        </section>

        <!-- AI 模型池 -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-brain</v-icon>
            <span class="ln-section-title">AI 模型池</span>
          </div>
          <div class="ln-body">
            <button
              v-for="p in ai?.profiles ?? []"
              :key="p.name"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': ai?.current === p.name }"
              :disabled="savingAi !== null"
              @click="pickAi(p.name)"
            >
              <span class="pool-radio" :class="{ 'pool-radio--on': ai?.current === p.name }" />
              <div class="pool-main">
                <div class="pool-title">
                  {{ p.label }}
                  <span class="pool-tier">{{ TIER_LABEL[p.tier] ?? p.tier }}</span>
                </div>
                <div class="pool-sub c-muted">模型 {{ p.model }}</div>
              </div>
              <v-progress-circular v-if="savingAi === p.name" indeterminate size="16" width="2" color="primary" />
              <span v-else-if="ai?.current === p.name" class="pool-current">使用中</span>
            </button>
          </div>
        </section>

        <!-- 模型 -->
        <section v-if="(model?.profiles?.length ?? 0) > 0" class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-brain</v-icon>
            <span class="ln-section-title">模型</span>
          </div>
          <div class="ln-body">
            <p class="t-body c-muted mb-2" style="font-size: 0.82rem">
              芝士在这个项目里用哪个 Claude 模型。默认 <strong>Sonnet 5</strong>（均衡、最省 订阅额度）；复杂项目可切
              <strong>Opus 5</strong>（更强，但更快消耗额度）。
            </p>
            <button
              v-for="p in model?.profiles ?? []"
              :key="p.id"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': model?.current === p.id }"
              :disabled="savingModel !== null"
              @click="pickModel(p.id)"
            >
              <span class="pool-radio" :class="{ 'pool-radio--on': model?.current === p.id }" />
              <div class="pool-main">
                <div class="pool-title">
                  {{ p.label }}
                  <span class="pool-tier">{{ p.price }}</span>
                </div>
                <div class="pool-sub c-muted">{{ p.description }}</div>
              </div>
              <v-progress-circular v-if="savingModel === p.id" indeterminate size="16" width="2" color="primary" />
              <span v-else-if="model?.current === p.id" class="pool-current">使用中</span>
            </button>
          </div>
        </section>

        <!-- 环境 (spec §9.1): which sandbox image the agent runs in -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-package-variant-closed</v-icon>
            <span class="ln-section-title">环境镜像</span>
          </div>
          <div class="ln-body">
            <!-- Default (pool base image) -->
            <button
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': !env?.current }"
              :disabled="savingEnv !== null"
              @click="pickEnv('')"
            >
              <span class="pool-radio" :class="{ 'pool-radio--on': !env?.current }" />
              <div class="pool-main">
                <div class="pool-title">知是基座（默认）</div>
                <div class="pool-sub c-muted">{{ env?.default }}</div>
              </div>
              <v-progress-circular
                v-if="savingEnv === '__default__'"
                indeterminate
                size="16"
                width="2"
                color="primary"
              />
              <span v-else-if="!env?.current" class="pool-current">使用中</span>
            </button>
            <!-- Curated images (e.g. cheesex-dev for dogfooding on this repo) -->
            <button
              v-for="o in env?.options ?? []"
              :key="o.image"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': env?.current === o.image }"
              :disabled="savingEnv !== null"
              @click="pickEnv(o.image)"
            >
              <span class="pool-radio" :class="{ 'pool-radio--on': env?.current === o.image }" />
              <div class="pool-main">
                <div class="pool-title">{{ o.label }}</div>
                <div class="pool-sub c-muted">{{ o.image }}</div>
              </div>
              <v-progress-circular v-if="savingEnv === o.image" indeterminate size="16" width="2" color="primary" />
              <span v-else-if="env?.current === o.image" class="pool-current">使用中</span>
            </button>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              基座装了 uv / node / git 等通用工具；cheesex-dev 额外预装了本仓库的 依赖，芝士可以直接在盒子里跑 cheesex
              自己的测试（dogfooding）。
            </p>
          </div>
        </section>

        <!-- 上游仓库 (spec §6.3): link an existing repo, keep pulling it in -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-source-branch-sync</v-icon>
            <span class="ln-section-title">上游仓库</span>
          </div>
          <div class="ln-body">
            <div class="d-flex align-center" style="gap: 8px">
              <v-text-field
                v-model="upstreamUrl"
                density="compact"
                variant="outlined"
                hide-details
                placeholder="https://… 或本机绝对路径（留空 = 取消关联）"
                style="flex: 1"
                @keydown.enter="saveUpstream"
              />
              <v-btn size="small" variant="tonal" :loading="savingUpstream" @click="saveUpstream"> 保存 </v-btn>
              <v-btn
                size="small"
                color="primary"
                variant="flat"
                :disabled="!upstreamSaved"
                :loading="syncing"
                @click="doSyncUpstream"
              >
                同步上游
              </v-btn>
            </div>
            <p
              v-if="syncResult"
              class="t-body mt-2"
              style="font-size: 0.8rem"
              :class="syncResult.synced ? 'c-muted' : 'text-error'"
            >
              <template v-if="syncResult.synced && (syncResult.commits ?? 0) > 0">
                已合入上游 {{ syncResult.commits }} 个提交。
              </template>
              <template v-else-if="syncResult.synced">已是最新，没有新提交。</template>
              <template v-else>同步失败：{{ syncResult.reason }}</template>
            </p>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              关联一个已有的 git 仓库，把它的历史拉进这个项目；之后随时同步新提交。 有冲突时会原样中止，不会合一半。
            </p>
          </div>
        </section>

        <!-- 连接 GitHub 仓库 (#192): cheesex-app 安装到具体仓库, 之后该项目的
             git 操作走这个 installation 的短时 token -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-github</v-icon>
            <span class="ln-section-title">连接 GitHub 仓库</span>
          </div>
          <div class="ln-body">
            <v-alert
              v-if="githubCallbackNotice"
              :type="githubCallbackNotice.type"
              density="comfortable"
              closable
              class="mb-3"
              @click:close="githubCallbackNotice = null"
            >
              {{ githubCallbackNotice.text }}
            </v-alert>
            <div v-if="githubConnection?.connected" class="d-flex align-center" style="gap: 8px">
              <v-icon size="18" color="success">mdi-check-circle</v-icon>
              <span class="t-body">
                已连接 <strong>{{ githubConnection.repo }}</strong>
              </span>
              <v-spacer />
              <v-btn size="small" variant="tonal" :loading="connectingGithubRepo" @click="connectGithubRepo">
                重新连接
              </v-btn>
            </div>
            <div v-else class="d-flex align-center" style="gap: 8px">
              <span class="t-body c-muted">尚未连接仓库</span>
              <v-spacer />
              <v-btn
                size="small"
                color="primary"
                variant="flat"
                :loading="connectingGithubRepo"
                @click="connectGithubRepo"
              >
                连接 GitHub 仓库
              </v-btn>
            </div>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              通过 cheesex-app 把这个项目接到一个 GitHub 仓库；之后沙箱看 CI/CD 用的短时 token
              会按这个连接铸造，不用再手工配凭据。
            </p>
          </div>
        </section>

        <!-- 连接 GitHub 账号 (#192): App 的 user-to-server 授权, 独立于经典
             OAuth 登录 —— 记录"这个人是哪个 GitHub 账号", 供 credit 归属 +
             两阶段采纳代表身份开 PR 用 -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-account-box-outline</v-icon>
            <span class="ln-section-title">连接 GitHub 账号</span>
          </div>
          <div class="ln-body">
            <!-- 加载中 -->
            <div v-if="githubAccountLoadState === 'loading'" class="d-flex align-center" style="gap: 8px">
              <v-progress-circular indeterminate size="16" width="2" color="primary" />
              <span class="t-body c-muted">正在加载连接状态…</span>
            </div>

            <!-- 请求失败: never fall through to the "未连接" look, that would lie -->
            <div v-else-if="githubAccountLoadState === 'error'" class="d-flex align-center" style="gap: 8px">
              <v-icon size="18" color="error">mdi-alert-circle-outline</v-icon>
              <span class="t-body text-error">{{ githubAccountLoadError ?? '加载连接状态失败' }}</span>
              <v-spacer />
              <v-btn size="small" variant="text" @click="loadGithubAccountConnection">重试</v-btn>
            </div>

            <!-- 已连接 -->
            <template v-else-if="githubAccountConn">
              <div class="d-flex align-center" style="gap: 8px">
                <v-icon size="18" color="success">mdi-check-circle</v-icon>
                <span class="t-body">
                  已连接 <strong>{{ githubAccountConn.login ?? githubAccountConn.providerUserId }}</strong>
                  <span v-if="githubAccountConn.connectedAt" class="c-faint" style="font-size: 0.8rem">
                    （{{ relTime(githubAccountConn.connectedAt) }}连接）
                  </span>
                </span>
                <v-spacer />
                <v-btn size="small" variant="tonal" :loading="connectingGithubAccount" @click="connectGithubAccount">
                  重新连接
                </v-btn>
                <v-btn
                  size="small"
                  variant="text"
                  color="error"
                  :loading="disconnectingGithubAccount"
                  @click="disconnectGithubAccount"
                >
                  断开
                </v-btn>
              </div>
              <v-alert
                v-if="isGithubAccountTokenExpired(githubAccountConn)"
                type="warning"
                density="comfortable"
                variant="tonal"
                class="mt-2"
              >
                GitHub 授权已过期：采纳你参与的话题时将无法用你的身份自动开 PR，会退回为直接合并到
                main。请点击「重新连接」刷新授权。
              </v-alert>
            </template>

            <!-- 未连接 -->
            <div v-else class="d-flex align-center" style="gap: 8px">
              <span class="t-body c-muted">把你自己的 GitHub 账号和这个平台身份关联起来</span>
              <v-spacer />
              <v-btn size="small" variant="tonal" :loading="connectingGithubAccount" @click="connectGithubAccount">
                连接 GitHub 账号
              </v-btn>
            </div>

            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              用于把你 merge 的提交正确归到你名下（committer credit），也是两阶段采纳能代表你身份开 PR
              的前提。跟登录用的 GitHub 账号授权是两回事，可以是同一个 GitHub 账号，也可以不是。
            </p>
          </div>
        </section>
      </template>
    </v-container>

    <!-- 新建角色: the Claude Code agents-file fields — name/title/description
         (frontmatter) + persona body. -->
    <v-dialog v-model="roleDialog" max-width="620">
      <v-card>
        <v-card-title class="pt-4">新建专家角色</v-card-title>
        <v-card-text class="pb-0">
          <v-text-field
            v-model="newRoleName"
            label="名称（英文标识）"
            placeholder="data-science"
            hint="小写字母、数字和 .-_，作为角色的唯一 ID"
            persistent-hint
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-text-field
            v-model="newRoleTitle"
            label="标题"
            placeholder="数据科学"
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-text-field
            v-model="newRoleDescription"
            label="描述（一句话，显示在下拉里）"
            placeholder="统计分析、机器学习与数据可视化"
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-textarea
            v-model="newRoleBody"
            label="Persona 正文（注入芝士的 system prompt）"
            placeholder="你是一位数据科学导师，擅长……"
            rows="6"
            density="compact"
            variant="outlined"
            auto-grow
          />
          <v-alert v-if="roleFormError" type="error" density="compact" class="mb-2">
            {{ roleFormError }}
          </v-alert>
        </v-card-text>
        <v-card-actions class="px-6 pb-4">
          <v-spacer />
          <v-btn variant="text" @click="roleDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creatingRole"
            :disabled="!newRoleName.trim() || !newRoleBody.trim()"
            @click="submitNewRole"
          >
            创建并使用
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.settings-page {
  background: var(--canvas);
}
/* Section rhythm (the ln-* classes are scoped to OverviewView, so style them
   here). Flat sections: a title, then the pool rows are the cards. */
.ln-section {
  margin-bottom: 26px;
}
.ln-section-head {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.ln-section-title {
  font-size: 15px;
  font-weight: 600;
}
.ln-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pool-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 10px;
  background: var(--surface);
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s,
    box-shadow 0.15s;
}
.pool-row:hover:not(:disabled) {
  border-color: rgba(var(--v-theme-primary), 0.5);
}
.pool-row--active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.05);
  box-shadow: 0 0 0 1px rgb(var(--v-theme-primary));
}
.pool-row:disabled {
  cursor: default;
  opacity: 0.7;
}
.pool-radio {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid rgba(var(--v-border-color), 0.9);
}
.pool-radio--on {
  border-color: rgb(var(--v-theme-primary));
  background: radial-gradient(circle, rgb(var(--v-theme-primary)) 0 4px, transparent 5px);
}
.pool-main {
  flex: 1;
  min-width: 0;
}
.pool-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.pool-tier {
  font-size: 0.68rem;
  font-weight: 500;
  padding: 1px 7px;
  border-radius: 10px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
}
.pool-sub {
  font-size: 0.8rem;
  margin-top: 2px;
}
.pool-current {
  font-size: 0.74rem;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.link {
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>
