<script setup lang="ts">
import type {
  BranchProtection,
  BranchProtectionPatch,
  ForgeAttribution,
  ForgeConnection,
  OAuthConnectionInfo,
  ProjectMemberRow,
} from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  connectGithubRepo as apiConnectGithubRepo,
  deleteOAuthConnection,
  getBranchProtection,
  getForgeAttribution,
  getForgeConnection,
  getGithubAccountAuthorizeUrl,
  getUpstream,
  listOAuthConnections,
  listProjectMembers,
  setBranchProtection,
  setForgeAttribution,
  setUpstream,
} from '../api'
import ProjectComputeSettings from '../components/ProjectComputeSettings.vue'
import ProjectDefaultModelSettings from '../components/ProjectDefaultModelSettings.vue'
import ProjectEnvironmentSettings from '../components/ProjectEnvironmentSettings.vue'
import AgentTeamSettings from '../components/settings/AgentTeamSettings.vue'
import CreditsPanel from '../components/settings/CreditsPanel.vue'
import { parseApprovalsInput, parseCheckPaths } from '../lib/branchProtection'
import {
  explainAccountLinkFailure,
  explainRepoInstallFailure,
  findGithubAccountConnection,
  isGithubAccountTokenExpired,
} from '../lib/githubAccount'
import { relTime } from '../lib/relTime'
import { myId } from '../me'
import { SudoCancelledError, withSudo } from '../utils/sudo'

import { t } from '@/i18n'
import ProjectPage from '@/views/workspace/ProjectPage.vue'

// Project defaults and favorites never change an already running room.
const props = defineProps<{ projectId: string }>()
const router = useRouter()
const route = useRoute()

// 从 true 起步：挂载那一帧设置还没读回来，先画一帧空的表单再换成转圈就是一闪。
const loading = ref(true)
const error = ref<string | null>(null)
// 上游仓库: the linked repo URL as edited, plus save/sync state and last result.
const upstreamUrl = ref('')
const savingUpstream = ref(false)

// GitHub App install flow (#192): repo connection is read-only status here —
// connecting/reconnecting happens on github.com, not in this form.
const forgeConnection = ref<ForgeConnection | null>(null)
const attribution = ref<ForgeAttribution | null>(null)
const attributionSaving = ref(false)
const attributionError = ref<string | null>(null)
const attributionChoice = computed(() =>
  attribution.value?.requester_coauthor == null ? 'default' : attribution.value.requester_coauthor ? 'on' : 'off'
)
const attributionItems = computed(() => [
  { title: `跟随系统默认（${attribution.value?.deployment_default ? '开启' : '关闭'}）`, value: 'default' },
  { title: '开启', value: 'on' },
  { title: '关闭', value: 'off' },
])

async function saveAttribution(choice: string) {
  attributionSaving.value = true
  attributionError.value = null
  try {
    attribution.value = await setForgeAttribution(props.projectId, choice === 'default' ? null : choice === 'on')
  } catch (e) {
    attributionError.value = e instanceof Error ? e.message : '保存失败，请重试'
  } finally {
    attributionSaving.value = false
  }
}
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

// Unbinding needs the person to confirm who they are first.
async function disconnectGithubAccount() {
  const userId = myId()
  const connectionId = githubAccountConn.value?.id
  if (!userId || connectionId === undefined) return
  disconnectingGithubAccount.value = true
  try {
    await withSudo('oauth:unbind', async (sudoTicket) => {
      await deleteOAuthConnection(userId, connectionId, sudoTicket)
      githubAccountConn.value = null
    })
  } catch (e) {
    if (e instanceof SudoCancelledError) return
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

async function load() {
  loading.value = true
  error.value = null
  try {
    const [upP, forge, credit] = await Promise.all([
      getUpstream(props.projectId),
      getForgeConnection(props.projectId),
      getForgeAttribution(props.projectId),
    ])
    upstreamUrl.value = upP.url ?? ''
    forgeConnection.value = forge
    attribution.value = credit
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设置失败'
  } finally {
    loading.value = false
  }
}

// Save (or with an empty field, unlink) the upstream repo URL.
async function saveUpstream() {
  savingUpstream.value = true
  try {
    const r = await setUpstream(props.projectId, upstreamUrl.value.trim())
    upstreamUrl.value = r.url ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存上游仓库失败'
  } finally {
    savingUpstream.value = false
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
      forgeConnection.value = await getForgeConnection(props.projectId)
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
  loadBranchProtection()
  loadGithubAccountConnection()
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
  <ProjectPage class="settings-page" :title="t('navigation.project.settings')">
    <template #actions>
      <v-btn append-icon="mdi-storefront-outline" @click="router.push({ name: 'market' })">市场</v-btn>
    </template>
    <div v-if="loading" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
      {{ error }}
    </v-alert>

    <template v-else>
      <!-- 分四组，因为这一页的读者一次只为一件事来：换队友 / 调机器 / 定交付
             规则 / 接仓库。原来是六块竖着铺满一页，读的人得自己认哪块是哪块；而
             没绑仓库的项目从头到尾只看得到跟仓库有关的东西，于是整页像是坏的。 -->
      <h2 class="t-title settings-group">队友</h2>
      <AgentTeamSettings :project-id="projectId" />

      <section class="page-section">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-brain</v-icon>
          <span class="page-section-title">默认模型</span>
        </div>
        <div class="page-section-body">
          <ProjectDefaultModelSettings :project-id="projectId" />
        </div>
      </section>

      <h2 class="t-title settings-group">运行环境</h2>
      <section class="page-section">
        <ProjectComputeSettings :project-id="projectId" />
      </section>
      <ProjectEnvironmentSettings :project-id="projectId" />
      <CreditsPanel :project-id="projectId" />

      <h2 class="t-title settings-group">交付</h2>

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
                <div class="bp-hint c-faint">点名的检查全部通过才能合并；填了路径范围的检查只在改到对应文件时要求</div>
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
                  autocomplete="off"
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
                  autocomplete="off"
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
                  color="primary"
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
                autocomplete="off"
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
                autocomplete="off"
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

      <h2 class="t-title settings-group">仓库</h2>

      <!-- 上游仓库 (spec §6.3): link an existing repo, keep pulling it in -->
      <section v-if="forgeConnection?.kind === 'github_app' && !forgeConnection.connected" class="page-section">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
          <span class="page-section-title">GitHub 仓库地址</span>
        </div>
        <div class="page-section-body">
          <div class="d-flex align-center" style="gap: 8px">
            <v-text-field
              v-model="upstreamUrl"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              placeholder="https://github.com/组织或用户名/仓库名"
              style="flex: 1"
              @keydown.enter="saveUpstream"
            />
            <v-btn size="small" color="primary" variant="tonal" :loading="savingUpstream" @click="saveUpstream">
              保存
            </v-btn>
          </div>
          <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
            填写要连接的 GitHub 仓库地址并保存，再点击下方“连接 GitHub 仓库”。连接后，代码与 PR 都保留在该仓库。
          </p>
        </div>
      </section>

      <section v-if="forgeConnection?.kind === 'forgejo'" class="page-section" data-testid="forge-repository">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
          <span class="page-section-title">代码仓库</span>
        </div>
        <div class="page-section-body">
          <div class="d-flex align-center flex-wrap" style="gap: 8px">
            <v-icon v-if="forgeConnection.connected" size="18" color="success">mdi-check-circle</v-icon>
            <span class="t-body">{{ forgeConnection.connected ? '由芝士托管' : '仓库正在准备中' }}</span>
            <v-spacer />
            <v-btn
              v-if="forgeConnection.url"
              :href="forgeConnection.url"
              target="_blank"
              rel="noopener noreferrer"
              size="small"
              variant="tonal"
            >
              打开仓库
            </v-btn>
          </div>
          <p class="t-body c-faint mt-2" style="font-size: 0.8rem">项目创建后，暂不支持切换托管服务。</p>
        </div>
      </section>

      <!-- 连接 GitHub 仓库 (#192): cheesex-app 安装到具体仓库, 之后该项目的
             git 操作走这个 installation 的短时 token -->
      <section v-if="forgeConnection?.kind === 'github_app'" class="page-section" data-testid="github-repository">
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
          <div v-if="forgeConnection.connected" class="d-flex align-center" style="gap: 8px">
            <v-icon size="18" color="success">mdi-check-circle</v-icon>
            <span class="t-body">
              已连接 <strong>{{ forgeConnection.repo }}</strong>
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

      <section class="page-section" data-testid="forge-attribution">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-account-edit-outline</v-icon>
          <span class="page-section-title">提交署名</span>
        </div>
        <div class="page-section-body">
          <v-alert v-if="attributionError" type="error" density="compact" class="mb-3">{{ attributionError }}</v-alert>
          <v-select
            autocomplete="off"
            :model-value="attributionChoice"
            :items="attributionItems"
            label="将任务请求者列为共同作者"
            :loading="attributionSaving"
            :disabled="attributionSaving"
            hide-details
            @update:model-value="saveAttribution"
          />
          <p class="t-body c-muted mt-2">{{ attribution?.effective ? '当前已开启' : '当前已关闭' }}</p>
          <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
            开启后，新提交会附上任务请求者的共同作者署名，提交作者仍为 AI 队友。这项设置不会修改已有提交。
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
                @click="disconnectGithubAccount()"
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
              GitHub 授权已过期，暂时无法以你的身份创建 PR。请点击“重新连接”刷新授权。
            </v-alert>
          </template>

          <!-- 未连接 -->
          <div v-else class="d-flex align-center" style="gap: 8px">
            <span class="t-body c-muted">暂无关联账号</span>
            <v-spacer />
            <v-btn
              size="small"
              color="primary"
              variant="tonal"
              :loading="connectingGithubAccount"
              @click="connectGithubAccount"
            >
              连接 GitHub 账号
            </v-btn>
          </div>

          <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
            用于识别你的提交署名，并以你的身份创建 GitHub PR。这与登录用的 GitHub 授权相互独立，可以连接不同的账号。
          </p>
        </div>
      </section>
    </template>
  </ProjectPage>
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
/* 组标题：比区块标题重一档，前后留白把这一页切成四段读得出来的东西。第一组不
   留上边距——它紧接着页头。 */
.settings-group {
  margin: 32px 0 12px;
  color: var(--ink);
}
.settings-group:first-of-type {
  margin-top: 0;
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
