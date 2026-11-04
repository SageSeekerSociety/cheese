// 「芝士眼中的你」那一段：删掉一条。先从列表里拿掉，再去删；服务器不肯就放回原处
// （回到原来那一格，不是列表末尾）。
import type { ProfileUnderstanding } from '@/cx_types'
import type { ProfilePage } from './useProfilePage'

import { deleteUnderstanding } from '@/api'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'
import { patchQuery } from '@/query/client'

/**
 * @param pageKey 这一页在缓存里的那一份。
 * @param data 缓存里现在画着的那一份。
 */
export function useProfileNotes(pageKey: () => readonly unknown[], data: () => ProfilePage | undefined) {
  async function forget(note: ProfileUnderstanding) {
    const key = pageKey()
    const notes = data()?.profile.understanding
    const at = notes?.findIndex((n) => n.id === note.id) ?? -1
    if (at < 0) return
    const withNotes = (change: (notes: ProfileUnderstanding[]) => ProfileUnderstanding[]) =>
      patchQuery<ProfilePage>(key, (held) => ({
        ...held,
        profile: { ...held.profile, understanding: change(held.profile.understanding ?? []) },
      }))
    await withNotes((list) => list.filter((n) => n.id !== note.id))
    try {
      await deleteUnderstanding(note.id)
    } catch {
      await withNotes((list) =>
        list.some((n) => n.id === note.id) ? list : [...list.slice(0, at), note, ...list.slice(at)]
      )
      toast.error(t('users.profile.notes.deleteFailed'))
    }
  }

  return { forget }
}
