// 文档的读法（./docRead）连着 tiptap、ProseMirror 和整套块的 schema，压缩后两百多 KB。
// 频道页、引用条都要用，但首屏不必等它：第一次用到时才加载。
import { shallowRef } from 'vue'

type DocRead = typeof import('./docRead')

const loaded = shallowRef<DocRead | null>(null)
let loading: Promise<DocRead> | null = null

export function loadDocRead(): Promise<DocRead> {
  loading ??= import('./docRead').then((m) => (loaded.value = m))
  return loading
}

/** 加载好了就给出来；还没有就开始加载，先给 null。在 computed 或模板里调用，加载完会重算。 */
export function docReadNow(): DocRead | null {
  if (!loaded.value) void loadDocRead()
  return loaded.value
}
