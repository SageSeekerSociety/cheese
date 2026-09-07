<script setup lang="ts">
import type {
  AgentType,
  BranchProtection,
  BranchProtectionPatch,
  GithubConnection,
  ModelProfiles,
  OAuthConnectionInfo,
  ProjectMemberRow,
  SandboxImageInfo,
  UpstreamSyncResult,
} from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  connectGithubRepo as apiConnectGithubRepo,
  createAgentType,
  deleteOAuthConnection,
  getBranchProtection,
  getGithubAccountAuthorizeUrl,
  getGithubConnection,
  getModelProfiles,
  getProject,
  getSandboxImage,
  getUpstream,
  listAgentTypes,
  listOAuthConnections,
  listProjectAgents,
  listProjectMembers,
  setBranchProtection,
  setModelProfile,
  setProjectAgentType,
  setSandboxImage,
  setUpstream,
  syncUpstream,
} from '../api'
import { parseApprovalsInput, parseCheckPaths } from '../lib/branchProtection'
import {
  explainAccountLinkFailure,
  explainRepoInstallFailure,
  findGithubAccountConnection,
  isGithubAccountTokenExpired,
} from '../lib/githubAccount'
import { relTime } from '../lib/relTime'
import { myHandle, myId } from '../me'

// Project settings. Compute is intentionally absent: execution-architecture v4
// places the pool/default on the team and the per-run choice on the topic.
const props = defineProps<{ projectId: string }>()
const router = useRouter()
const route = useRoute()

const projectName = ref('')
const model = ref<ModelProfiles | null>(null)
const savingModel = ref<string | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const env = ref<SandboxImageInfo | null>(null)
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
// TWO refs, not one: the 仓库 flow and the 账号 flow are separate sections with
// separate buttons, and a single shared notice rendered inside the 仓库 section
// put 「已连接 GitHub 账号。」 under the 「连接 GitHub 仓库」 heading — the result
// of one action announced above a different one.
type CallbackNotice = { type: 'success' | 'error' | 'info'; text: string }
const githubRepoNotice = ref<CallbackNotice | null>(null)
const githubAccountNotice = ref<CallbackNotice | null>(null)

// 连接 GitHub 账号 status: loaded independently from `load()` (its own
// loading/error state) so a failure here can't be mistaken for "not
// connected" — see the section's four-branch template below.
type GithubAccountLoadState = 'loading' | 'loaded' | 'error'
const githubAccountLoadState = ref<GithubAccountLoadState>('loading')
const githubAccountLoadError = ref<string | null>(null)
const githubAccountConn = ref<OAuthConnectionInfo | null>(null)
const disconnectingGithubAccount = ref(false)

async function loadGithubAccountConnection() {
  const userId = myId()
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
  const userId = myId()
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

// 分支保护 (#718): its own three-state load (like 连接 GitHub 账号 above) —
// the GET asks GitHub for a protection snapshot, so it can be slower than the
// rest of the page and must not hold the main Promise.all hostage.
type BranchProtectionLoadState = 'loading' | 'loaded' | 'error'
const bpLoadState = ref<BranchProtectionLoadState>('loading')
const bpLoadError = ref<string | null>(null)
const bp = ref<BranchProtection | null>(null)
const bpMembers = ref<ProjectMemberRow[]>([])
const bpError = ref<string | null>(null)
// Which rule's save is in flight ('' = none): every control disables while any
// save runs (same as the pool rows), the spinner sits on the one being saved.
const bpSaving = ref<string | null>(null)
const newCheckName = ref('')
const newCheckPaths = ref('')
// approvals_required is edited through a draft string so an invalid entry can
// be rejected and snapped back without ever writing a bad value into bp.
const approvalsDraft = ref('1')

// GitHub 自己开了保护时，平台的同名规则灰掉（#718 拍板②：两处都能改就是两套
// 配置）。同名 = 出现在 GitHub 分支保护那一页的规则：必须通过的检查、跟上
// main、作废已有采纳、批准人数、放行名单。自动合并和任务默认 reviewer 是平台
// 自己的概念，保持可编。status === 'unknown' 查不到 ≠ 已开启，不灰。
const ghEnforced = computed(() => bp.value?.github_protection.enforced ?? false)
const bpBusy = computed(() => bpSaving.value !== null)

const bpMemberItems = computed(() =>
  bpMembers.value
    .filter((m) => !m.agent)
    .map((m) => ({
      title: m.name ? `${m.name}（${m.user_handle}）` : m.user_handle,
      value: m.user_handle,
    }))
)
const bpReviewerItems = computed(() => [{ title: '未指定', value: '' }, ...bpMemberItems.value])

async function loadBranchProtection() {
  bpLoadState.value = 'loading'
  bpLoadError.value = null
  try {
    const [rules, membersP] = await Promise.all([
      getBranchProtection(props.projectId),
      listProjectMembers(props.projectId),
    ])
    bp.value = rules
    bpMembers.value = membersP.data
    approvalsDraft.value = String(rules.approvals_required)
    bpLoadState.value = 'loaded'
  } catch (e) {
    bpLoadError.value = e instanceof Error ? e.message : '加载分支保护规则失败'
    bpLoadState.value = 'error'
  }
}

// One PUT per control change. On failure bp stays untouched, so every control
// (all bound to bp, never to local copies) snaps back by itself.
async function saveBranchProtection(patch: BranchProtectionPatch, key: string): Promise<boolean> {
  if (!bp.value) return false
  bpError.value = null
  bpSaving.value = key
  try {
    const rules = await setBranchProtection(props.projectId, patch)
    bp.value = { ...bp.value, ...rules }
    approvalsDraft.value = String(rules.approvals_required)
    return true
  } catch (e) {
    bpError.value = e instanceof Error ? e.message : '保存分支保护规则失败'
    return false
  } finally {
    bpSaving.value = null
  }
}

async function addRequiredCheck() {
  if (!bp.value) return
  const name = newCheckName.value.trim()
  if (!name) return
  const paths = parseCheckPaths(newCheckPaths.value)
  const next = [...bp.value.required_checks, paths.length ? { name, paths } : { name }]
  if (await saveBranchProtection({ required_checks: next }, 'required_checks')) {
    newCheckName.value = ''
    newCheckPaths.value = ''
  }
}

async function removeRequiredCheck(index: number) {
  if (!bp.value) return
  const next = bp.value.required_checks.filter((_, i) => i !== index)
  await saveBranchProtection({ required_checks: next }, 'required_checks')
}

async function saveApprovals() {
  if (!bp.value) return
  const parsed = parseApprovalsInput(approvalsDraft.value)
  if (parsed === null) {
    bpError.value = '批准人数要是不小于 1 的整数'
    approvalsDraft.value = String(bp.value.approvals_required)
    return
  }
  if (parsed === bp.value.approvals_required) return
  if (!(await saveBranchProtection({ approvals_required: parsed }, 'approvals_required'))) {
    approvalsDraft.value = String(bp.value.approvals_required)
  }
}

async function saveOverrideHandles(handles: string[]) {
  // 空名单 = 回到缺省（owner + lead），后端用 null 表达。
  await saveBranchProtection({ override_handles: handles.length ? handles : null }, 'override_handles')
}

// Which type this project's 芝士 wears. The catalog merges preset types with
// custom ones; '' = 芝士 with no specialty. The type sits on the AGENT, not on
// the project — switching it re-skins the 芝士 that is already here and leaves
// the memory it has accumulated exactly where it is.
const agentTypes = ref<AgentType[]>([])
const agentTypeCurrent = ref('')
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
  ...agentTypes.value.map((r) => ({
    name: r.name,
    title: r.builtin ? r.title : `${r.title || r.name}（自定义）`,
    description: r.description,
  })),
])

async function pickRole(name: string | null) {
  const next = name ?? ''
  savingRole.value = true
  try {
    const agent = await setProjectAgentType(props.projectId, next)
    agentTypeCurrent.value = agent.type_name ?? ''
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
    const created = await createAgentType({
      name: newRoleName.value.trim(),
      title: newRoleTitle.value.trim(),
      description: newRoleDescription.value.trim(),
      body: newRoleBody.value.trim(),
      created_by: myHandle(),
    })
    // A custom role shadows its built-in namesake — keep one entry per name.
    agentTypes.value = [...agentTypes.value.filter((r) => r.name !== created.name), created]
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

async function load() {
  loading.value = true
  error.value = null
  try {
    const [proj, modelP, envP, upP, typesP, agentsP, ghP] = await Promise.all([
      getProject(props.projectId),
      getModelProfiles(props.projectId),
      getSandboxImage(props.projectId),
      getUpstream(props.projectId),
      listAgentTypes(),
      listProjectAgents(props.projectId),
      getGithubConnection(props.projectId),
    ])
    projectName.value = proj.name
    model.value = modelP
    env.value = envP
    upstreamSaved.value = upP.url
    upstreamUrl.value = upP.url ?? ''
    agentTypes.value = typesP.data
    agentTypeCurrent.value = agentsP.data.find((a) => a.is_default)?.type_name ?? ''
    githubConnection.value = ghP
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设置失败'
  } finally {
    loading.value = false
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
      githubRepoNotice.value = { type: 'success', text: `已连接 ${res.repo}` }
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

  const reason = route.query.reason as string | undefined
  if (install === 'success') {
    const repo = route.query.repo as string | undefined
    githubRepoNotice.value = { type: 'success', text: `已连接仓库 ${repo ?? ''}`.trim() }
  } else if (install === 'pending') {
    githubRepoNotice.value = { type: 'info', text: '安装请求已提交，等待组织管理员批准' }
  } else if (install === 'error') {
    githubRepoNotice.value = { type: 'error', text: explainRepoInstallFailure(reason) }
  } else if (account === 'success') {
    githubAccountNotice.value = { type: 'success', text: '已连接 GitHub 账号' }
  } else if (account === 'error') {
    githubAccountNotice.value = { type: 'error', text: explainAccountLinkFailure(reason) }
  }

  const { github_install, github_account, repo: _repo, reason: _reason, ...rest } = route.query
  void github_install
  void github_account
  void _repo
  void _reason
  router.replace({ query: rest })
}

onMounted(() => {
  consumeGithubCallbackNotice()
  load()
  loadGithubAccountConnection()
  loadBranchProtection()
})
watch(
  () => props.projectId,
  () => {
    load()
    loadBranchProtection()
  }
)
</script>

<template>
  <div class="settings-page fill-height overflow-y-auto">
    <v-container class="py-6 page-container">
      <div class="d-flex align-center mb-4">
        <v-spacer />
        <v-btn
          variant="text"
          size="small"
          append-icon="mdi-storefront-outline"
          @click="router.push({ name: 'market' })"
        >
          市场
        </v-btn>
      </div>

      <div class="mb-6">
        <div class="t-eyebrow mb-1">项目设置 · {{ projectName }}</div>
        <h1 class="t-page-title">资源池</h1>
        <p class="t-body c-muted mt-1" style="max-width: 640px">
          选择这个项目使用哪套 AI
          模型、运行在哪套算力上。默认都是知是自己的资源池，开箱即用；需要更强的模型或专属机器，可以在
          <a class="link" @click="router.push({ name: 'market' })">市场</a> 里挑选。
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
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-account-school-outline</v-icon>
            <span class="page-section-title">专家角色</span>
          </div>
          <div class="page-section-body">
            <div class="d-flex align-center" style="gap: 8px">
              <v-select
                :model-value="agentTypeCurrent"
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
              芝士以这个专家身份进驻项目，影响它的口吻和关注点。平台内置了几个角色，也可以为机构或自己定义新的。
            </p>
          </div>
        </section>

        <!-- Project default model -->
        <section v-if="model" class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-brain</v-icon>
            <span class="page-section-title">项目默认模型</span>
          </div>
          <div class="page-section-body">
            <p v-if="model.supply === 'gateway'" class="t-body c-muted mb-2">当前使用平台模型池，模型由平台统一配置</p>
            <p v-else class="t-body c-muted mb-2">
              当前使用 Claude 订阅。以下选择作为项目默认模型；专家角色单独指定模型时，优先使用角色的模型
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
              <span v-else-if="model?.current === p.id" class="pool-current">已设置为默认</span>
            </button>
          </div>
        </section>

        <!-- 环境 (spec §9.1): which sandbox image the agent runs in -->
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-package-variant-closed</v-icon>
            <span class="page-section-title">环境镜像</span>
          </div>
          <div class="page-section-body">
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
                <div class="pool-title">默认镜像</div>
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
              默认镜像装了 uv / node / git 等通用工具；cheesex-dev 额外预装了本仓库的依赖，芝士可以直接在里面运行本仓库
              自己的测试。
            </p>
          </div>
        </section>

        <!-- 上游仓库 (spec §6.3): link an existing repo, keep pulling it in -->
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-source-branch-sync</v-icon>
            <span class="page-section-title">上游仓库</span>
          </div>
          <div class="page-section-body">
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
                已合入上游 {{ syncResult.commits }} 个提交
              </template>
              <template v-else-if="syncResult.synced">已是最新，没有新提交</template>
              <template v-else>同步失败：{{ syncResult.reason }}</template>
            </p>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              关联一个已有的 git
              仓库，把它的历史拉进这个项目；之后可随时同步新提交。有冲突时会原样中止，不会只合并一部分。
            </p>
          </div>
        </section>

        <!-- 分支保护 (#718): 平台侧的合并规则，照 GitHub 分支保护那一页的顺序。
             GitHub 自己开了保护时同名规则灰掉（拍板②），说明见 ghEnforced 的注释。 -->
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-shield-outline</v-icon>
            <span class="page-section-title">分支保护</span>
          </div>
          <div class="page-section-body">
            <!-- 加载中 -->
            <div v-if="bpLoadState === 'loading'" class="d-flex align-center" style="gap: 8px">
              <v-progress-circular indeterminate size="16" width="2" color="primary" />
              <span class="t-body c-muted">正在加载分支保护规则…</span>
            </div>

            <!-- 加载失败 -->
            <div v-else-if="bpLoadState === 'error'" class="d-flex align-center" style="gap: 8px">
              <v-icon size="18" color="error">mdi-alert-circle-outline</v-icon>
              <span class="t-body text-error">{{ bpLoadError ?? '加载分支保护规则失败' }}</span>
              <v-spacer />
              <v-btn size="small" variant="text" @click="loadBranchProtection">重试</v-btn>
            </div>

            <template v-else-if="bp">
              <!-- GitHub 已开保护: 顶行提示 + 同名规则灰掉；查不到状态只说明，不灰 -->
              <div v-if="bp.github_protection.enforced" class="bp-github-note">
                <v-icon size="16" class="c-muted">mdi-github</v-icon>
                <span>GitHub 已在执行以下规则</span>
              </div>
              <p v-else-if="bp.github_protection.status === 'unknown'" class="t-body c-faint" style="font-size: 0.8rem">
                暂时查不到 GitHub 侧的保护状态，以下规则按平台配置执行
              </p>

              <v-alert v-if="bpError" type="error" density="compact" closable @click:close="bpError = null">
                {{ bpError }}
              </v-alert>

              <!-- 1. 合并前必须通过的检查 -->
              <div class="bp-row bp-row--stack">
                <div class="bp-main">
                  <div class="bp-label">合并前必须通过的检查</div>
                  <div class="bp-hint c-faint">
                    点名的检查全部通过才能合并；填了路径范围的检查只在改到对应文件时要求
                  </div>
                </div>
                <div v-if="bp.required_checks.length === 0" class="bp-hint c-muted">暂无必须通过的检查</div>
                <div v-for="(c, i) in bp.required_checks" :key="`${c.name}-${i}`" class="bp-check">
                  <span class="bp-check-name">{{ c.name }}</span>
                  <span v-if="c.paths?.length" class="bp-check-paths c-muted">{{ c.paths.join('、') }}</span>
                  <span v-else class="bp-check-paths c-faint">所有文件</span>
                  <v-spacer />
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    title="移除这条检查"
                    :disabled="ghEnforced || bpBusy"
                    @click="removeRequiredCheck(i)"
                  >
                    <v-icon size="16">mdi-close</v-icon>
                  </v-btn>
                </div>
                <div class="d-flex align-center" style="gap: 8px">
                  <v-text-field
                    v-model="newCheckName"
                    density="compact"
                    variant="outlined"
                    hide-details
                    placeholder="检查名"
                    style="flex: 1"
                    :disabled="ghEnforced || bpBusy"
                    @keydown.enter="addRequiredCheck"
                  />
                  <v-text-field
                    v-model="newCheckPaths"
                    density="compact"
                    variant="outlined"
                    hide-details
                    placeholder="路径范围，如 backend/**，可留空"
                    style="flex: 1"
                    :disabled="ghEnforced || bpBusy"
                    @keydown.enter="addRequiredCheck"
                  />
                  <v-btn
                    size="small"
                    variant="tonal"
                    :disabled="ghEnforced || !newCheckName.trim()"
                    :loading="bpSaving === 'required_checks'"
                    @click="addRequiredCheck"
                  >
                    添加
                  </v-btn>
                </div>
              </div>

              <!-- 2. strict -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">合并前分支必须跟上 main</div>
                  <div class="bp-hint c-faint">开启后落后的分支由平台先更新再合并</div>
                </div>
                <v-switch
                  density="compact"
                  color="primary"
                  hide-details
                  :model-value="bp.strict"
                  :disabled="ghEnforced || bpBusy"
                  :loading="bpSaving === 'strict' ? 'primary' : false"
                  @update:model-value="saveBranchProtection({ strict: !!$event }, 'strict')"
                />
              </div>

              <!-- 3. dismiss_stale -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">新提交作废已有的采纳</div>
                  <div class="bp-hint c-faint">这里推送代码的通常是芝士，新的提交需要重新采纳，所以默认开启</div>
                </div>
                <v-switch
                  density="compact"
                  color="primary"
                  hide-details
                  :model-value="bp.dismiss_stale"
                  :disabled="ghEnforced || bpBusy"
                  :loading="bpSaving === 'dismiss_stale' ? 'primary' : false"
                  @update:model-value="saveBranchProtection({ dismiss_stale: !!$event }, 'dismiss_stale')"
                />
              </div>

              <!-- 4. auto_merge_allowed（平台自己的概念，不随 GitHub 灰掉） -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">允许自动合并</div>
                  <div class="bp-hint c-faint">开启后，被规则拦住的采纳可以选择在检查全部通过时自动合并</div>
                </div>
                <v-switch
                  density="compact"
                  color="primary"
                  hide-details
                  :model-value="bp.auto_merge_allowed"
                  :disabled="bpBusy"
                  :loading="bpSaving === 'auto_merge_allowed' ? 'primary' : false"
                  @update:model-value="saveBranchProtection({ auto_merge_allowed: !!$event }, 'auto_merge_allowed')"
                />
              </div>

              <!-- 5. override_handles -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">人工放行的人</div>
                  <div class="bp-hint c-faint">检查未过时可以放行合并的人；留空时是项目 owner 和 lead</div>
                </div>
                <v-select
                  density="compact"
                  variant="outlined"
                  hide-details
                  multiple
                  chips
                  closable-chips
                  placeholder="owner 和 lead"
                  style="max-width: 320px"
                  :items="bpMemberItems"
                  :model-value="bp.override_handles ?? []"
                  :disabled="ghEnforced || bpBusy"
                  :loading="bpSaving === 'override_handles'"
                  @update:model-value="saveOverrideHandles"
                />
              </div>

              <!-- 6. approvals_required -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">需要几个人批准</div>
                  <div class="bp-hint c-faint">采纳数达到这个数量才会合并</div>
                </div>
                <v-text-field
                  v-model="approvalsDraft"
                  type="number"
                  min="1"
                  density="compact"
                  variant="outlined"
                  hide-details
                  style="max-width: 96px"
                  :disabled="ghEnforced || bpBusy"
                  :loading="bpSaving === 'approvals_required'"
                  @change="saveApprovals"
                  @keydown.enter="saveApprovals"
                />
              </div>

              <!-- 7. merge_method（只读附注） -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">合并方式</div>
                  <div class="bp-hint c-faint">绑定 GitHub 的项目从仓库设置读取，这里不可修改</div>
                </div>
                <span class="bp-check-name">{{ bp.merge_method }}</span>
              </div>

              <!-- 8. default_reviewer（平台自己的概念，不随 GitHub 灰掉） -->
              <div class="bp-row">
                <div class="bp-main">
                  <div class="bp-label">任务默认 reviewer</div>
                  <div class="bp-hint c-faint">派任务没有指定 reviewer 时用这个人</div>
                </div>
                <v-select
                  density="compact"
                  variant="outlined"
                  hide-details
                  style="max-width: 320px"
                  :items="bpReviewerItems"
                  :model-value="bp.default_reviewer"
                  :disabled="bpBusy"
                  :loading="bpSaving === 'default_reviewer'"
                  @update:model-value="saveBranchProtection({ default_reviewer: $event ?? '' }, 'default_reviewer')"
                />
              </div>
            </template>
          </div>
        </section>

        <!-- 连接 GitHub 仓库 (#192): cheesex-app 安装到具体仓库, 之后该项目的
             git 操作走这个 installation 的短时 token -->
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-github</v-icon>
            <span class="page-section-title">连接 GitHub 仓库</span>
          </div>
          <div class="page-section-body">
            <v-alert
              v-if="githubRepoNotice"
              :type="githubRepoNotice.type"
              density="comfortable"
              closable
              class="mb-3"
              @click:close="githubRepoNotice = null"
            >
              {{ githubRepoNotice.text }}
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
              <span class="t-body c-muted">暂无关联仓库</span>
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
              通过 cheesex-app 把这个项目接到一个 GitHub 仓库；之后芝士查看 CI/CD 所需的临时凭据
              会按这个连接自动签发，不用再手工配置。
            </p>
          </div>
        </section>

        <!-- 连接 GitHub 账号 (#192): App 的 user-to-server 授权, 独立于经典
             OAuth 登录 —— 记录"这个人是哪个 GitHub 账号", 供 credit 归属 +
             两阶段采纳代表身份开 PR 用 -->
        <section class="page-section">
          <div class="page-section-head">
            <v-icon size="14" class="c-faint">mdi-account-box-outline</v-icon>
            <span class="page-section-title">连接 GitHub 账号</span>
          </div>
          <div class="page-section-body">
            <!-- The 账号 flow's own outcome, in the 账号 section. -->
            <v-alert
              v-if="githubAccountNotice"
              :type="githubAccountNotice.type"
              density="comfortable"
              closable
              class="mb-3"
              @click:close="githubAccountNotice = null"
            >
              {{ githubAccountNotice.text }}
            </v-alert>

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
              <span class="t-body c-muted">暂无关联账号</span>
              <v-spacer />
              <v-btn size="small" variant="tonal" :loading="connectingGithubAccount" @click="connectGithubAccount">
                连接 GitHub 账号
              </v-btn>
            </div>

            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              用于把你合并的提交正确归到你名下，也是采纳时能代表你的身份开 PR 的前提。这与登录用的 GitHub
              授权是两回事，可以是同一个账号，也可以不是。
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
            label="名称（英文）"
            placeholder="data-science"
            hint="小写字母、数字和 .-_"
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
            label="描述"
            placeholder="统计分析、机器学习与数据可视化"
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-textarea
            v-model="newRoleBody"
            label="角色设定（可留空）"
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
            :disabled="!newRoleName.trim()"
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
/* 内容区是侧栏 (--canvas) 上面那张 surface —— 和话题视图、总览同一层关系。 */
.settings-page {
  background: var(--surface);
}
/* 区块节奏。这里的区块本来就不是卡片（卡片是下面那些 .pool-row —— 一个池子、
   一个镜像是真正可拿起的对象）；区块标题跟着总览一起降成 eyebrow，划分靠留白
   加一条顶部发丝线。类名同步改掉：`ln-section` 是这个仓库对「白卡片区块」的叫
   法，留着它会让下一个人以为这里还有卡片可以照抄。 */
.page-section {
  padding-top: 18px;
  margin-bottom: 22px;
  border-top: 1px solid var(--line);
}
.page-section-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.page-section-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--faint);
}
.page-section-body {
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
  border-radius: var(--radius-md);
  /* 根面已经是 surface，再刷一层 surface 就是白底压白底 —— 这一行的边界由描边
     给，底色留给 hover。 */
  background: transparent;
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s,
    box-shadow 0.15s;
}
.pool-row:hover:not(:disabled) {
  border-color: rgba(var(--v-theme-primary), 0.5);
  background: var(--fill);
}
/* 选中态自己有底色，hover 不该把它冲淡。 */
.pool-row--active:hover:not(:disabled) {
  background: rgba(var(--v-theme-primary), 0.05);
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
  /* 小标签 → --radius-sm，和 .chip-neutral / .ln-tag 同档（原来是 10px，不在阶梯上）。 */
  border-radius: var(--radius-sm);
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
/* 分支保护 (#718)。规则行：左边名称 + 说明，右边控件；GitHub 已执行时控件
   disabled（Vuetify 自己降透明度），行本身不动 —— 灰掉不是藏起来。 */
.bp-github-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 13px;
}
.bp-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}
.bp-row--stack {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.bp-main {
  flex: 1;
  min-width: 0;
}
.bp-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}
.bp-hint {
  font-size: 12px;
  margin-top: 1px;
}
.bp-check {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.bp-check-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  padding: 1px 6px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--text);
}
.bp-check-paths {
  font-size: 12px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
