// 资料库里一份文件的每一版：取回来、下载某一版、把旧版恢复成当前。画面在
// `views/library/LibraryVersionsDialog.vue`，它只摆、只报动作；这里管请求。
//
// 「恢复为当前」把选中的旧版**复制**成新的一版——历史只增不减，被恢复的那一版和它
// 之后的几版都还在。恢复之后引用这个名字的消息读到的就是它。
import type { LibraryShelf, LibraryVersion } from '@/lib/libraryApi'

import { ref, watch } from 'vue'

import { downloadFile } from '@/api'
import { t } from '@/i18n'
import { libraryVersionRawUrl, listLibraryVersions, restoreLibraryVersion } from '@/lib/libraryApi'

export function useLibraryVersions(
  shelf: () => LibraryShelf,
  path: () => string | null,
  restored: (path: string, version: number) => void
) {
  const versions = ref<LibraryVersion[]>([])
  const loading = ref(false)
  const failed = ref<string | null>(null)
  const actionError = ref('')
  /** 正在恢复的是哪一版（null = 没有在恢复）：只让那一行转圈。 */
  const restoring = ref<string | null>(null)
  let session = 0

  async function load() {
    const name = path()
    const id = ++session
    versions.value = []
    failed.value = null
    actionError.value = ''
    if (!name) return
    loading.value = true
    try {
      const list = await listLibraryVersions(shelf(), name)
      if (id === session) versions.value = list
    } catch (e) {
      if (id === session) failed.value = e instanceof Error ? e.message : t('work.library.versionsFailed')
    } finally {
      if (id === session) loading.value = false
    }
  }
  watch(path, load, { immediate: true })

  async function download(version: LibraryVersion) {
    const name = path()
    if (!name) return
    // 成功和失败各弹一句，在 `downloadFile` 那一层说（`lib/download`）；这里不留红字。
    await downloadFile(libraryVersionRawUrl(shelf(), name, version.id), name.split('/').pop() || name)
  }

  async function restore(version: LibraryVersion) {
    const name = path()
    if (!version.id || !name) return
    restoring.value = version.id
    actionError.value = ''
    try {
      await restoreLibraryVersion(shelf(), name, version.id)
      restored(name, version.version)
      await load()
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : t('work.library.restoreFailed')
    } finally {
      restoring.value = null
    }
  }

  return { versions, loading, failed, actionError, restoring, load, download, restore }
}
