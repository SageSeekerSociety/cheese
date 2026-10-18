// 关联 GitHub：从哪一页开始，就回到哪一页。
//
// 关联可以从三处开始——账号安全、项目的「仓库与署名」、任务里「PR 由 AI 队友创建」那一行
// ——授权页回跳时带着 `?github_account=success|error&reason=`。哪一页都可能接到它，所以
// 读结果的那一半装在外框上（App.vue），不在某一页里。
import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { getGithubAccountAuthorizeUrl } from '@/api'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { explainAccountLinkFailure } from '@/lib/githubAccount'

/** Send the browser to GitHub's authorization page; it comes back to `returnPath`. */
export async function linkGithubAccount(returnPath: string): Promise<void> {
  try {
    const { url } = await getGithubAccountAuthorizeUrl(returnPath)
    goAuthorize(url)
  } catch (e) {
    toast.error(explainAccountLinkFailure(e instanceof Error ? e.message : undefined))
  }
}

/** Say how a 关联 GitHub that just came back went, then drop it from the address. */
export function useGithubLinkOutcome(): void {
  const route = useRoute()
  const router = useRouter()
  watch(
    () => route.query.github_account,
    (outcome) => {
      if (outcome !== 'success' && outcome !== 'error') return
      if (outcome === 'success') toast.success(t('work.projectSettings.repoFlow.accountConnected'))
      else toast.error(explainAccountLinkFailure(route.query.reason as string | undefined))
      const { github_account: _outcome, reason: _reason, ...rest } = route.query
      void _outcome
      void _reason
      void router.replace({ query: rest })
    },
    { immediate: true }
  )
}
