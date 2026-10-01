// 项目设置页「仓库」这一组的数据：上游仓库地址、仓库连接、提交署名、连接 GitHub
// 账号，和两条回跳（`?github_install=` / `?github_account=`）带回来的结果。
//
// 拆自 `views/ProjectSettingsView.vue`（#2143，那一页 1041 行）：取数与保存在这一层，
// 画在 `components/settings/*.vue`（只吃 props、只往上发事件），页面只接线。
//
// **这一组里有两条独立的加载**，各带自己的 loading/error：
//
//   1. 连接 GitHub 账号 —— 一次失败不能画成「未连接」，那是撒谎（见下面那段注释）；
//   2. 分支保护 —— 在 `useBranchProtection` 里，GET 要问 GitHub，可能比这一页慢。
//
// **两条回跳各有一条 notice**，不是一条共用的：仓库流程和账号流程是两个区块、两颗
// 按钮，共用一条会把一次动作的结果报在另一件事的标题下面（`githubSettingsSections.spec.ts`
// 钉的就是这件事）。失败也落在自己那一条里，不落进页面级的 `error` —— 那一块会顶掉
// 整页，上面没有按钮可以再试，也说不清是哪一次动作败了。
import type { ForgeAttribution, ForgeConnection, OAuthConnectionInfo } from '@/cx_types'

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { SudoCancelledError, withSudo } from '@/utils/sudo'

import {
  connectGithubRepo as apiConnectGithubRepo,
  deleteOAuthConnection,
  getForgeAttribution,
  getForgeConnection,
  getGithubAccountAuthorizeUrl,
  getUpstream,
  listOAuthConnections,
  setForgeAttribution,
  setUpstream,
} from '@/api'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { explainAccountLinkFailure, explainRepoInstallFailure, findGithubAccountConnection } from '@/lib/githubAccount'
import { myId } from '@/me'

/** 两条回跳共同的结果形状。error 也走这里：它落回发起它的那一块。 */
export type CallbackNotice = { type: 'success' | 'error' | 'info'; text: string }

/** 连接 GitHub 账号那一块自己的四态。 */
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
  const attribution = ref<ForgeAttribution | null>(null)
  const attributionSaving = ref(false)
  const attributionError = ref<string | null>(null)
  const attributionChoice = computed(() =>
    attribution.value?.requester_coauthor == null ? 'default' : attribution.value.requester_coauthor ? 'on' : 'off'
  )
  const attributionItems = computed(() => [
    {
      title: t('work.projectSettings.repoFlow.followDefault', {
        state: attribution.value?.deployment_default
          ? t('work.projectSettings.repoFlow.on')
          : t('work.projectSettings.repoFlow.off'),
      }),
      value: 'default',
    },
    { title: t('work.projectSettings.repoFlow.on'), value: 'on' },
    { title: t('work.projectSettings.repoFlow.off'), value: 'off' },
  ])

  async function saveAttribution(choice: string) {
    attributionSaving.value = true
    attributionError.value = null
    try {
      attribution.value = await setForgeAttribution(projectId(), choice === 'default' ? null : choice === 'on')
    } catch (e) {
      attributionError.value = e instanceof Error ? e.message : t('work.projectSettings.repoFlow.saveFailed')
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
  // A failed click lands in its section's notice too, never in the page-level
  // `error`: that one replaces the whole page with a banner, leaving no button
  // to try again and no hint of which action it belongs to.
  const githubRepoNotice = ref<CallbackNotice | null>(null)
  const githubAccountNotice = ref<CallbackNotice | null>(null)

  // 连接 GitHub 账号 status: loaded independently from `load()` (its own
  // loading/error state) so a failure here can't be mistaken for "not
  // connected" — see the section's four-branch template below.
  const githubAccountLoadState = ref<GithubAccountLoadState>('loading')
  const githubAccountLoadError = ref<string | null>(null)
  const githubAccountConn = ref<OAuthConnectionInfo | null>(null)
  const disconnectingGithubAccount = ref(false)

  async function loadGithubAccountConnection() {
    const userId = myId()
    if (!userId) {
      githubAccountLoadState.value = 'error'
      githubAccountLoadError.value = t('work.projectSettings.repoFlow.notSignedIn')
      return
    }
    githubAccountLoadState.value = 'loading'
    githubAccountLoadError.value = null
    try {
      const { connections } = await listOAuthConnections(userId)
      githubAccountConn.value = findGithubAccountConnection(connections)
      githubAccountLoadState.value = 'loaded'
    } catch (e) {
      githubAccountLoadError.value = e instanceof Error ? e.message : t('work.projectSettings.githubAccount.loadFailed')
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
      githubAccountNotice.value = {
        type: 'error',
        text: e instanceof Error ? e.message : t('work.projectSettings.repoFlow.disconnectFailed'),
      }
    } finally {
      disconnectingGithubAccount.value = false
    }
  }

  async function load() {
    loading.value = true
    error.value = null
    try {
      const [upP, forge, credit] = await Promise.all([
        getUpstream(projectId()),
        getForgeConnection(projectId()),
        getForgeAttribution(projectId()),
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

  // Same shape, but for the App's user-to-server "连接 GitHub 账号" — a
  // separate identity link, not a repo connection (github_account_link.py).
  async function connectGithubAccount() {
    connectingGithubAccount.value = true
    try {
      const { url } = await getGithubAccountAuthorizeUrl(projectId())
      if (goAuthorize(url)) connectingGithubAccount.value = false
    } catch (e) {
      githubAccountNotice.value = {
        type: 'error',
        text: e instanceof Error ? e.message : t('work.projectSettings.repoFlow.authUrlFailed'),
      }
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
    } else if (account === 'success') {
      githubAccountNotice.value = { type: 'success', text: t('work.projectSettings.repoFlow.accountConnected') }
    } else if (account === 'error') {
      githubAccountNotice.value = { type: 'error', text: explainAccountLinkFailure(reason) }
    }

    const { github_install, github_account, repo: _repo, reason: _reason, holder: _holder, ...rest } = route.query
    void github_install
    void github_account
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
    attributionChoice,
    attributionItems,
    saveAttribution,
    connectingGithubRepo,
    connectingGithubAccount,
    githubRepoNotice,
    githubAccountNotice,
    githubAccountLoadState,
    githubAccountLoadError,
    githubAccountConn,
    disconnectingGithubAccount,
    loadGithubAccountConnection,
    disconnectGithubAccount,
    load,
    saveUpstream,
    connectGithubRepo,
    connectGithubAccount,
    consumeGithubCallbackNotice,
  }
}
