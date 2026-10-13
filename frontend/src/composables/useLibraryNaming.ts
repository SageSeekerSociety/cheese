// 资料库里给东西起名字的那一套：挪动或改名一份文件、一个文件夹，在这一层新建文件夹。
//
// 名字就是位置，所以挪动和改名是同一个请求。新建文件夹不发请求：文件夹在里面有文件
// 时才存在，新建就是走进那一层，往里放东西它就在了。
import { computed, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { listLibraryFolders, moveLibraryFile } from '@/lib/libraryApi'
import { folderOf, leafOf } from '@/lib/libraryFolders'

type Pending = { path: string } | { newFolder: true }

export function useLibraryNaming(page: {
  projectId: () => string
  /** 现在看着的那一层。 */
  dir: () => string
  goTo: (dir: string) => void
  /** 正打开着的那一份（没有是 `''`）。 */
  opened: () => string
  /** 打开着的那一份挪走了：改去打开它的新名字。 */
  reopen: (path: string) => void
  reload: () => Promise<void>
}) {
  const pending = ref<Pending | null>(null)
  const error = ref('')
  const moving = ref(false)
  /** 「移动到」能挑的文件夹：打开名字框时问一次。 */
  const folders = ref<string[]>([])

  function start(next: Pending) {
    error.value = ''
    pending.value = next
    folders.value = []
    if ('path' in next)
      void listLibraryFolders(page.projectId())
        .then((all) => {
          if (pending.value === next) folders.value = all
        })
        .catch(() => {
          // 挑不了现成的文件夹，照样能自己写一个。
        })
  }

  // 名字框打开时填好的样子。
  const dialog = computed(() => {
    const target = pending.value
    if (target && 'path' in target)
      return {
        title: t('work.library.moveTitle'),
        primaryLabel: t('work.library.moveConfirm'),
        name: leafOf(target.path),
        folder: folderOf(target.path),
        folders: folders.value,
      }
    return { title: t('work.library.newFolder'), primaryLabel: t('work.library.newFolderConfirm') }
  })

  async function submit(to: string) {
    const target = pending.value
    if (!target) return
    if ('newFolder' in target) {
      pending.value = null
      page.goTo(page.dir() ? `${page.dir()}/${to}` : to)
      return
    }
    moving.value = true
    error.value = ''
    try {
      await moveLibraryFile(page.projectId(), target.path, to)
      pending.value = null
      toast(t('work.library.moved'))
      const opened = page.opened()
      if (opened === target.path || opened.startsWith(`${target.path}/`))
        page.reopen(to + opened.slice(target.path.length))
      await page.reload()
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.library.moveFailed')
    } finally {
      moving.value = false
    }
  }

  return { pending, error, moving, dialog, start, submit }
}
