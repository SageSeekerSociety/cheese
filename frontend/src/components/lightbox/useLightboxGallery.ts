// 看图浮层左右切换的那一串图：这段对话里的所有图，从点开的那张开始。
//
// 点开的那张，地址是调用方（AttachmentImage）取好字节转出来的，归它还。切到别的图时
// 这里自己去取字节、转 object URL，这些是这里的，切走不还（再切回来不用重取），浮层
// 关掉时一起还掉。底部缩略图条只取当前这张前后几张，一段长对话里几十张图，没人翻到的
// 不该一张张去下。
import type { AttachmentSource } from '@/lib/attachmentSource'
import type { ChatImageActions } from '@/lib/chatImages'

import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

/** 缩略图条上当前这张前后各取几张。 */
export const FILM_REACH = 5

export function fileName(path: string): string {
  return path.split('/').pop() || path
}

export function useLightboxGallery(
  props: { src: string; alt: string; topicId?: string | null; path?: string | null },
  source: AttachmentSource,
  actions: ChatImageActions | null
) {
  const paths = computed<readonly string[]>(() => {
    const path = props.path
    if (!path) return ['']
    const all = actions?.gallery() ?? []
    return all.includes(path) ? all : [path]
  })
  const start = Math.max(0, paths.value.indexOf(props.path ?? ''))
  const index = ref(start)
  /** 这一刻看的是哪一张：浮层开着的时候对话还会来消息、删消息，`paths` 跟着变，
   *  只记一个位置会让浮层换到别的图上（或者落到数组外面，卡在「加载中」）。 */
  const shown = ref(paths.value[start] ?? '')
  /** 这里自己取来的地址（不含点开的那张）。 */
  const urls = reactive(new Map<string, string>())
  const failed = reactive(new Set<string>())
  const asked = new Set<string>()
  let disposed = false

  function urlAt(i: number): string {
    const path = paths.value[i]
    if (path === (props.path ?? '')) return props.src
    return urls.get(path) ?? ''
  }

  function ensure(i: number) {
    const path = paths.value[i]
    if (path === undefined || path === (props.path ?? '') || asked.has(path) || !props.topicId) return
    asked.add(path)
    source.imageUrl(props.topicId, path).then(
      (url) => {
        if (disposed) URL.revokeObjectURL(url)
        else urls.set(path, url)
      },
      () => failed.add(path)
    )
  }

  watch(
    index,
    (i) => {
      ensure(i)
      for (let d = 1; d <= FILM_REACH; d += 1) {
        ensure(i - d)
        ensure(i + d)
      }
    },
    { immediate: true }
  )

  const total = computed(() => paths.value.length)
  const current = computed(() => urlAt(index.value))
  const currentPath = computed(() => paths.value[index.value] ?? '')
  const name = computed(() =>
    currentPath.value === (props.path ?? '') ? props.alt : fileName(currentPath.value) || props.alt
  )
  const currentFailed = computed(() => failed.has(currentPath.value))

  function go(delta: number) {
    const n = total.value
    if (n < 2) return
    index.value = (index.value + delta + n) % n
    shown.value = paths.value[index.value] ?? shown.value
  }

  // 这一串图变了（对话里来了新消息、删了一条）：还留在原来那张上；那张没了就退到
  // 现在这一串里最近的合法位置，绝不会落在数组外面。
  watch(paths, (list) => {
    const at = list.indexOf(shown.value)
    index.value = at >= 0 ? at : Math.min(index.value, Math.max(0, list.length - 1))
    shown.value = list[index.value] ?? ''
  })

  onBeforeUnmount(() => {
    disposed = true
    for (const url of urls.values()) URL.revokeObjectURL(url)
    urls.clear()
  })

  return { paths, index, total, current, currentPath, currentFailed, name, urlAt, go }
}
