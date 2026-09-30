// What the page shows about the desktop app itself: its own update, the 关于
// dialog (DesktopAboutDialog.vue) and the 在手机上使用 dialog
// (DesktopPhoneDialog.vue). The dialogs sit at the root of the page (App.vue)
// because the menus that open them close as they do. One update status for the
// whole page, read from the app once and then kept current by what it reports.
import { ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import {
  checkDesktopUpdates,
  desktopCan,
  type DesktopUpdateStatus,
  desktopUpdateStatus,
  onDesktopShowAbout,
  onDesktopUpdateStatus,
  restartDesktopToUpdate,
} from '@/lib/desktopApp'

const status = ref<DesktopUpdateStatus | null>(null)
const aboutOpen = ref(false)
const phoneOpen = ref(false)
const restarting = ref(false)
let following = false

function follow() {
  if (following || !desktopCan('updates')) return
  following = true
  desktopUpdateStatus()
    .then((now) => {
      // An event that arrived first is newer than this answer.
      if (now && status.value === null) status.value = now
    })
    .catch(() => {})
  onDesktopUpdateStatus((now) => (status.value = now))
  onDesktopShowAbout(() => (aboutOpen.value = true))
}

export function useDesktopApp() {
  follow()
  return {
    status,
    aboutOpen,
    phoneOpen,
    /** The app can report and check its own updates; an older app cannot. */
    canCheck: desktopCan('updates'),
    async check() {
      const now = await checkDesktopUpdates().catch(() => ({ state: 'failed' }) as const)
      if (now) status.value = now
    },
    restarting,
    /** The person's click on 重启以完成更新. On success the app restarts and this page goes with it. */
    async restart() {
      restarting.value = true
      try {
        await restartDesktopToUpdate()
      } catch (e) {
        toast.error(
          String(e) === 'connecting'
            ? t('navigation.desktopApp.busyConnecting')
            : t('navigation.desktopApp.restartFailed')
        )
      } finally {
        restarting.value = false
      }
    },
  }
}
