// 「分享…」那张框的取数和动作（#3774）：打开时问这份文档有没有链接、自己能不能分享，
// 创建、停止、复制都在这里。框本身（`components/library/DocShareDialog.vue`）只吃 props。
import { computed, reactive } from 'vue'

import { getDocumentShare, shareDocument, shareUrl, unshareDocument } from '../api/documentShares'
import { getDocumentAbout } from '../api/projectDocuments'
import { copyLink } from '../commands/copy'

import { t } from '@/i18n'

export function useDocumentShare() {
  const state = reactive({
    open: false,
    loading: false,
    busy: false,
    error: '',
    documentId: '',
    title: '',
    mayShare: false,
    token: null as string | null,
  })

  const url = computed(() => (state.token ? shareUrl(state.token) : null))

  const fail = (e: unknown) => {
    state.error = e instanceof Error ? e.message : t('work.room.doc.shareDialog.failed')
  }

  async function open(documentId: string) {
    Object.assign(state, { open: true, loading: true, busy: false, error: '', documentId, title: '', token: null })
    try {
      const [about, share] = await Promise.all([getDocumentAbout(documentId), getDocumentShare(documentId)])
      state.title = about.title || t('work.room.doc.untitled')
      state.mayShare = share.may_share
      state.token = share.link?.token ?? null
    } catch (e) {
      fail(e)
    } finally {
      state.loading = false
    }
  }

  async function act(run: (id: string) => Promise<{ link: { token: string } | null }>) {
    state.busy = true
    state.error = ''
    try {
      state.token = (await run(state.documentId)).link?.token ?? null
    } catch (e) {
      fail(e)
    } finally {
      state.busy = false
    }
  }

  /** 创建链接，建好就复制。 */
  async function share() {
    await act(shareDocument)
    if (url.value) await copyLink(url.value)
  }

  const unshare = () => act(unshareDocument)

  async function copy() {
    if (url.value) await copyLink(url.value)
  }

  return { state, url, open, share, unshare, copy }
}
