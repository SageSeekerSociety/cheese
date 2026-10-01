// 「提醒我」的那一次提交：发给后端、记着在不在发、没成的话为什么。对话框
// （`components/room/ReminderDialog.vue`）只管填，接口在这里。
import { ref } from 'vue'

import { setReminder } from '../api/reminders'

import { t } from '@/i18n'

export function useReminder() {
  const saving = ref(false)
  const error = ref<string | null>(null)

  /** 设成了回 true；没成的话原因在 `error` 里。 */
  async function set(topicId: string, at: Date, content: string): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      await setReminder(topicId, at, content)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.reminder.failed')
      return false
    } finally {
      saving.value = false
    }
  }

  return { saving, error, set }
}
