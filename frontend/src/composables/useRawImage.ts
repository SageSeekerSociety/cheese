// 一张要带 Authorization 才取得到的图（项目文件的 `/file/raw`、房间附件）：取回字节，
// 做成 object URL 交给 <img>。`<img src>` 直接挂那个地址是匿名请求，拿到的是一张裂图。
//
// object URL 把字节钉在内存里直到 revoke，所以换一张、不再要、卸载时都还回去。
import { onScopeDispose, ref, watch } from 'vue'

import { rawImageUrl } from '../api'

/** `url` 为空串时不取；`src` 是能挂上 <img> 的地址，取不到时 `error` 说为什么。 */
export function useRawImage(url: () => string) {
  const src = ref('')
  const error = ref('')
  let generation = 0

  function release() {
    if (src.value) URL.revokeObjectURL(src.value)
    src.value = ''
  }

  watch(
    url,
    async (next) => {
      const mine = ++generation
      release()
      error.value = ''
      if (!next) return
      try {
        const made = await rawImageUrl(next)
        if (mine !== generation) {
          URL.revokeObjectURL(made)
          return
        }
        src.value = made
      } catch (e) {
        if (mine === generation) error.value = e instanceof Error ? e.message : String(e)
      }
    },
    { immediate: true }
  )
  onScopeDispose(() => {
    generation += 1
    release()
  })

  return { src, error }
}
