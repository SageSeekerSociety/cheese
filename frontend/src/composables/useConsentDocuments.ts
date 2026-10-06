// 注册同意环节要交给后端的协议版本（#1486）：后端会拒绝与当前版本不符的同意，
// 所以不写死版本号。取数放在这里，`LegalConsent.vue` 只画同意环节本身——
// 页面容器调这个，把 `documents` / `loadError` 传给它。
import type { AcceptedDocuments } from '@/network/api/legal/types'

import { ref } from 'vue'

import { t } from '@/i18n'
import { LegalApi } from '@/network/api/legal'

export function useConsentDocuments() {
  /** 一份都没取到就是 null：`LegalConsent` 拿它判定「交不出同意」。 */
  const documents = ref<AcceptedDocuments | null>(null)
  const loadError = ref('')

  async function load(): Promise<AcceptedDocuments | null> {
    if (documents.value) return documents.value
    try {
      const { data } = await LegalApi.listDocuments()
      documents.value = Object.fromEntries(data.documents.map((d) => [d.document, d.version]))
      loadError.value = ''
    } catch {
      loadError.value = t('account.legalDocumentsUnavailable')
    }
    return documents.value
  }

  return { documents, loadError, load }
}
