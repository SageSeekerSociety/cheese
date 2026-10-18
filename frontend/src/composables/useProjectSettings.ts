// 项目设置页「仓库」这一组的数据：上游仓库地址、仓库连接、提交署名、你自己的 GitHub
// 账号（只读，管理在账号安全），和连接仓库回跳（`?github_install=`）带回来的结果。
//
// 拆自 `views/ProjectSettingsView.vue`（#2143，那一页 1041 行）：取数与保存在这一层，
// 画在 `components/settings/*.vue`（只吃 props、只往上发事件），页面只接线。
//
// **这一组里有两条独立的加载**，各带自己的 loading/error：
//
//   1. 你的 GitHub 账号 —— 一次失败不能画成「未关联」，那是撒谎；
//   2. 分支保护 —— 在 `useBranchProtection` 里，GET 要问 GitHub，可能比这一页慢。
//
// 连接仓库的结果落在仓库那一块自己的 notice 里，不落进页面级的 `error` —— 那一块会
// 顶掉整页，上面没有按钮可以再试。关联 GitHub 的结果由外框统一说（`useGithubAccountLink`）。
import type { CommitCredit, CommitCreditMode, ForgeConnection, OAuthConnectionInfo } from '@/cx_types'

import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  connectGithubRepo as apiConnectGithubRepo,
  getCommitCredit,
  getForgeConnection,
  getUpstream,
  listOAuthConnections,
  setCommitCredit,
  setUpstream,
} from '@/api'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { explainRepoInstallFailure, findGithubAccountConnection } from '@/lib/githubAccount'
import { myId } from '@/me'

/** 连接仓库那一块的结果。error 也走这里：它落回发起它的那一块。 */
export type CallbackNotice = { type: 'success' | 'error' | 'info'; text: string }

/** 你的 GitHub 账号那一行自己的三态。 */
export type GithubAccountLoadState = 'loading' | 'loaded' | 'error'

export function useProjectSettings(projectId: () => string) {
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
  const attribution = ref<CommitCredit | null>(null)
  const attributionSaving = ref(false)
  const attributionError = ref<string | null>(null)

  async function saveAttribution(mode: CommitCreditMode) {
    attributionSaving.value = true
    attributionError.value = null
    try {
      attribution.value = await setCommitCredit(projectId(), mode)
    } catch (e) {
      attributionError.value = e instanceof Error ? e.message : t('work.projectSettings.repoFlow.saveFailed')
    } finally {
      attributionSaving.value = false
    }
  }

  const connectingGithubRepo = ref(false)

  // Set from ?github_install= on the redirect back from github_install.py.
  // A failed click lands here too, never in the page-level `error`: that one
  // replaces the whole page with a banner, leaving no button to try again.
  const githubRepoNotice = ref<CallbackNotice | null>(null)

  // The viewer's own GitHub account, loaded on its own so a failed read can't
  // be mistaken for "not linked".
  const githubAccountLoadState = ref<GithubAccountLoadState>('loading')
  const githubAccountConn = ref<OAuthConnectionInfo | null>(null)

  async function loadGithubAccountConnection() {
    const userId = myId()
    if (!userId) {
      githubAccountLoadState.value = 'error'
      return
    }
    githubAccountLoadState.value = 'loading'
    try {
      const { connections } = await listOAuthConnections(userId)
      githubAccountConn.value = findGithubAccountConnection(connections)
      githubAccountLoadState.value = 'loaded'
    } catch {
      githubAccountLoadState.value = 'error'
    }
  }

  async function load() {
    loading.value = true
    error.value = null
    try {
      const [upP, forge, credit] = await Promise.all([
        getUpstream(projectId()),
        getForgeConnection(projectId()),
        getCommitCredit(projectId()),
      ])
      upstreamUrl.value = upP.url ?? ''
      forgeConnection.value = forge
      attribution.value = credit
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.repoFlow.loadFailed')
    } finally {
      loading.value = false
    }
  }

  // Save (or with an empty field, unlink) the upstream repo URL.
  async function saveUpstream() {
    savingUpstream.value = true
    try {
      const r = await setUpstream(projectId(), upstreamUrl.value.trim())
      upstreamUrl.value = r.url ?? ''
    } catch (e) {
      githubRepoNotice.value = {
        type: 'error',
        text: e instanceof Error ? e.message : t('work.projectSettings.repoFlow.upstreamSaveFailed'),
      }
    } finally {
      savingUpstream.value = false
    }
  }

  // To GitHub's install page; its callback (github_install.py) comes back here with ?github_install=<result>.
  async function connectGithubRepo() {
    connectingGithubRepo.value = true
    try {
      // Try an existing installation first: GitHub's install page dead-ends
      // (never fires the callback) when the App is already installed, so the
      // backend looks for an installation covering the upstream repo itself.
      const res = await apiConnectGithubRepo(projectId())
      if (res.connected) {
        forgeConnection.value = await getForgeConnection(projectId())
        githubRepoNotice.value = {
          type: 'success',
          text: t('work.projectSettings.githubRepo.connected', { repo: res.repo }),
        }
        connectingGithubRepo.value = false
        return
      }
      if (res.install_url) {
        if (goAuthorize(res.install_url)) connectingGithubRepo.value = false
        return
      }
      githubRepoNotice.value = { type: 'error', text: t('work.projectSettings.repoFlow.noInstallUrl') }
      connectingGithubRepo.value = false
    } catch (e) {
      githubRepoNotice.value = {
        type: 'error',
        text: e instanceof Error ? e.message : t('work.projectSettings.repoFlow.connectRepoFailed'),
      }
      connectingGithubRepo.value = false
    }
  }

  // The install callback redirects back to this exact page with a result in
  // the query string (no session/cookie hand-off — this page is the only
  // signal). Read it once, show it, then strip it so a refresh doesn't repeat it.
  function consumeGithubCallbackNotice() {
    const install = route.query.github_install as string | undefined
    if (!install) return

    const reason = route.query.reason as string | undefined
    if (install === 'success') {
      const repo = route.query.repo as string | undefined
      githubRepoNotice.value = {
        type: 'success',
        text: t('work.projectSettings.repoFlow.repoConnected', { repo: repo ?? '' }).trim(),
      }
    } else if (install === 'pending') {
      githubRepoNotice.value = { type: 'info', text: t('work.projectSettings.repoFlow.installPending') }
    } else if (install === 'error') {
      githubRepoNotice.value = {
        type: 'error',
        text: explainRepoInstallFailure(reason, {
          repo: route.query.repo as string | undefined,
          holder: route.query.holder as string | undefined,
        }),
      }
    }

    const { github_install, repo: _repo, reason: _reason, holder: _holder, ...rest } = route.query
    void github_install
    void _repo
    void _reason
    void _holder
    router.replace({ query: rest })
  }

  return {
    loading,
    error,
    upstreamUrl,
    savingUpstream,
    forgeConnection,
    attribution,
    attributionSaving,
    attributionError,
    saveAttribution,
    connectingGithubRepo,
    githubRepoNotice,
    githubAccountLoadState,
    githubAccountConn,
    loadGithubAccountConnection,
    load,
    saveUpstream,
    connectGithubRepo,
    consumeGithubCallbackNotice,
  }
}
