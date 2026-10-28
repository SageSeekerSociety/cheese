// 资料库里「看哪一份」：看的是哪一份记在地址上（`?file=`），刷新、把链接发给别人都回到
// 这一份。
//
// 已经取回来的那几页里有它就用那一份，没有（比如冷开一个链接，或者它在后面几页）就单独问
// 一次。开和关在手机上是一页的进出（`push`／退一格），桌面上只换右边那一栏（`replace`）；
// 关的时候往哪走由 `closeOverlay` 认来路（见 lib/backOut.ts）。
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'
import type { LibraryShelf } from '@/lib/libraryApi'
import type { LibraryFile } from '../lib/libraryApi'
import type { LibraryRow } from './useLibraryPages'

import { computed, type Ref, ref, watch } from 'vue'

import { closeOverlay } from '../lib/backOut'
import { getLibraryFile } from '../lib/libraryApi'

export function useLibrarySelection(page: {
  /** 哪一个资料库（`LibraryShelf`）。 */
  shelf: () => LibraryShelf
  /** 表里已经取回来的那些行。 */
  entries: Ref<LibraryRow[]>
  /** 同一个名字被替换以后加一：换过的那一份要重新问一次。 */
  revision: Ref<number>
  /** 桌面（右边一栏）还是手机（整一页）。 */
  mdAndUp: Ref<boolean>
  router: Router
  route: RouteLocationNormalizedLoaded
}) {
  /** 地址上点名的那一份（路径）；没有是 `''`。 */
  const selectedPath = computed(() => (typeof page.route.query.file === 'string' ? page.route.query.file : ''))
  // 已经取回来的那几页里没有的那一份，单独问回来的结果。
  const fetched = ref<LibraryFile | null>(null)
  const selected = computed(
    () =>
      page.entries.value.find((row) => row.file?.path === selectedPath.value)?.file ??
      (fetched.value?.path === selectedPath.value ? fetched.value : null)
  )
  watch(
    [selectedPath, () => page.shelf(), page.revision],
    async ([path, shelf]) => {
      if (!path || page.entries.value.some((row) => row.file?.path === path)) return
      try {
        const found = await getLibraryFile(shelf, path)
        if (selectedPath.value === path) fetched.value = found
      } catch {
        // 地址上那一份已经不在了：右边那一栏不摆，列表照常。
      }
    },
    { immediate: true }
  )

  function open(file: LibraryFile) {
    const next = { query: { ...page.route.query, file: file.path } }
    // 手机上是进了一页，返回键要能回到列表；桌面上只是换了右边那一栏。
    if (page.mdAndUp.value) void page.router.replace(next)
    else void page.router.push(next)
  }

  function close() {
    const rest = { ...page.route.query }
    delete rest.file
    // 手机上开一份文件是 push 进一页，所以这里退一格能真的把它弹掉；桌面上开一份是
    // replace（只换右边那一栏），退一格会退到资料库外面去 —— closeOverlay 认来路。
    closeOverlay(page.router, { query: rest })
  }

  return { selectedPath, selected, open, close }
}
