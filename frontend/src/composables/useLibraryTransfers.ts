// 资料库里的写：把东西放进来、换新、拿走、扔掉，各自在飞的时候界面上要显示的状态，以及
// 动手之前那一句确认。
//
// 放进来有两条路：对话里给（不经过这里）和在这一页上直接放——拖进来，或者选文件；放进
// 现在看着的那一层（`dir`）。换新是同一个名字换新的字节（旧的那一份留在版本里），所以
// 换过之后要重新读一次预览（`changed`）。扔掉不可逆，先问一句；扔的可能是整个文件夹，
// 里面的一起走。
//
// 只有 `busy` 里的那一个在飞，所以同一时刻屏幕上只有一处的按钮转着圈。
import type { LibraryShelf } from '@/lib/libraryApi'
import type { LibraryFile, LibraryFolder } from '../lib/libraryApi'
import type { LibraryRow } from './useLibraryPages'

import { type Ref, ref, watch } from 'vue'

import { deleteLibraryFile, downloadFile, libraryFileRawUrl } from '../api'
import { uploadPercent } from '../api/partUpload'
import { t } from '../i18n'
import { replaceLibraryFile, uploadLibraryFile } from '../lib/libraryApi'

import { toast } from '@/lib/toast'

export function useLibraryTransfers(page: {
  /** 哪一个资料库（`LibraryShelf`）。 */
  shelf: () => LibraryShelf
  /** 现在看着的那一层：放进来就放进这里。 */
  dir: () => string
  /** 清单变了：重新列一遍。 */
  reset: () => void
  /** 这几行不在了：不重新列，直接从表里拿掉。 */
  drop: (gone: (row: LibraryRow) => boolean) => void
  /** 正开着的那一份的路径；没有是 `''`。 */
  opened: () => string
  /** 打开着的那一份被扔掉了：把它关掉。 */
  close: () => void
  /** 同一个名字换了新的字节：预览要重新读。 */
  changed: () => void
  /** 页面顶上那条错误：动手之前先清掉，出错时写进原因。 */
  error: Ref<string>
}) {
  /** 正在动的那一份或那个文件夹（路径）；没有是 `''`。 */
  const busy = ref('')
  const uploading = ref(false)
  /** 正在传的那一份和它传到了百分之几：几 GB 的文件要传很久，不能只转一个圈。 */
  const progress = ref<{ name: string; percent: number } | null>(null)
  const track = (name: string) => (sent: number, total: number) =>
    (progress.value = { name, percent: uploadPercent(sent, total) })
  // 要删的那一份或那个文件夹（文件夹连同里面的一切）。
  const confirming = ref<LibraryFile | LibraryFolder | null>(null)
  const replacing = ref<{ target: LibraryFile; file: File } | null>(null)
  const dragging = ref(false)
  /** 「版本…」开着的是哪一份（null = 关着）。 */
  const versionsOf = ref<string | null>(null)

  const picker = ref<HTMLInputElement | null>(null)
  const replacePicker = ref<HTMLInputElement | null>(null)
  let replaceTarget: LibraryFile | null = null

  // 换了项目：上一条错误和没答完的那一句确认都不属于新的一份。
  watch(
    () => page.shelf(),
    () => {
      confirming.value = null
      page.error.value = ''
    }
  )

  async function upload(list: File[]) {
    if (!list.length) return
    uploading.value = true
    page.error.value = ''
    let done = 0
    try {
      for (const file of list) {
        await uploadLibraryFile(page.shelf(), file, page.dir(), track(file.name))
        done++
      }
    } catch (e) {
      page.error.value = e instanceof Error ? e.message : t('work.library.uploadFailed')
    } finally {
      uploading.value = false
      progress.value = null
      if (done) toast(t('work.library.uploaded', { n: done }))
      page.reset()
    }
  }

  function onPicked(event: Event) {
    const input = event.target as HTMLInputElement
    void upload(Array.from(input.files ?? []))
    input.value = ''
  }

  function onDrop(event: DragEvent) {
    dragging.value = false
    void upload(Array.from(event.dataTransfer?.files ?? []))
  }

  function pickReplacement(file: LibraryFile) {
    replaceTarget = file
    replacePicker.value?.click()
  }

  function onReplacementPicked(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (file && replaceTarget) replacing.value = { target: replaceTarget, file }
  }

  async function replace() {
    const pending = replacing.value
    if (!pending) return
    replacing.value = null
    busy.value = pending.target.path
    page.error.value = ''
    try {
      await replaceLibraryFile(page.shelf(), pending.target.path, pending.file, track(pending.file.name))
      page.changed()
      toast(t('work.library.replacedDone'))
      page.reset()
    } catch (e) {
      page.error.value = e instanceof Error ? e.message : t('work.library.replaceFailed')
    } finally {
      busy.value = ''
      progress.value = null
    }
  }

  /** 恢复了一版：预览换新、清单重读。 */
  function onRestored(_path: string, version: number) {
    page.changed()
    toast(t('work.library.restoredDone', { n: version }))
    page.reset()
  }

  async function download(file: LibraryFile) {
    // 成功和失败各弹一句，都在 `downloadFile` 那一层（`lib/download`）；资料库那一页
    // 不再为下载留一行红字。
    await downloadFile(libraryFileRawUrl(page.shelf(), file.path), file.path.split('/').pop() || file.path)
  }

  /** 确认框点「删除」：这时 `confirming` 一定是有值的，取出来交给 `remove`。 */
  function confirmRemove() {
    const target = confirming.value
    if (target) void remove(target)
  }

  async function remove(target: LibraryFile | LibraryFolder) {
    confirming.value = null
    busy.value = target.path
    page.error.value = ''
    const gone = (path: string) => path === target.path || path.startsWith(`${target.path}/`)
    try {
      await deleteLibraryFile(page.shelf(), target.path)
      page.drop((row) => gone(row.file?.path ?? row.folder?.path ?? ''))
      if (gone(page.opened())) page.close()
    } catch (e) {
      page.error.value = e instanceof Error ? e.message : t('work.library.deleteFailed')
    } finally {
      busy.value = ''
    }
  }

  return {
    busy,
    uploading,
    progress,
    confirming,
    replacing,
    dragging,
    versionsOf,
    picker,
    replacePicker,
    upload,
    onPicked,
    onDrop,
    pickReplacement,
    onReplacementPicked,
    replace,
    onRestored,
    download,
    confirmRemove,
    remove,
  }
}
