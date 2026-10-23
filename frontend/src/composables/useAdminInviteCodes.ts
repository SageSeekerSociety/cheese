// 方案与额度页（`/admin/credits`）上「邀请码」那一块的取数与动作：列出、生成、停用。
// 画法在 `components/admin/credits/AdminInviteCodes.vue`。
import type { AdminInviteCode } from '@/types/invites'

import { onMounted, ref } from 'vue'

import { createInviteCode, deactivateInviteCode, listInviteCodes } from '@/api/invites'
import { copyText } from '@/commands/copy'
import { t } from '@/i18n'

/** 服务端的原话在 `message` 里；取不到才用兜底那句。 */
function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

export function useAdminInviteCodes() {
  const codes = ref<AdminInviteCode[] | null>(null)
  const loadError = ref<string | null>(null)
  const busy = ref(false)
  const actionError = ref<string | null>(null)

  async function load() {
    loadError.value = null
    try {
      codes.value = (await listInviteCodes()).codes
    } catch (e) {
      codes.value = null
      loadError.value = message(e, t('credits.inviteCodes.loadFailed'))
    }
  }

  async function create(maxUses: number, note: string) {
    busy.value = true
    actionError.value = null
    try {
      await createInviteCode(maxUses, note.trim())
      await load()
      return true
    } catch (e) {
      actionError.value = message(e, t('credits.inviteCodes.createFailed'))
      return false
    } finally {
      busy.value = false
    }
  }

  async function deactivate(id: number) {
    busy.value = true
    actionError.value = null
    try {
      await deactivateInviteCode(id)
      await load()
      return true
    } catch (e) {
      actionError.value = message(e, t('credits.inviteCodes.deactivateFailed'))
      return false
    } finally {
      busy.value = false
    }
  }

  function copy(code: string) {
    void copyText(code, t('credits.inviteCodes.copied'))
  }

  onMounted(load)

  return { codes, loadError, busy, actionError, load, create, deactivate, copy }
}
