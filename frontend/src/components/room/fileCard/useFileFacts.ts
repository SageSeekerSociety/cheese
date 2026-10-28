// 一张文件卡片上「这份文件现在怎样了」：现在是第几版、多大，这条消息之后又存过没有。
//
// 消息块本身不记发出时是哪一版，所以按时间对：这条消息发出那一刻（多给几秒——芝士摆
// 出一份文件时，存版和发消息是前后脚，存版常常晚几百毫秒）之前存下的最后一版，就是
// 这条消息说的那一版；比它新的都是之后改的。
import type { Ref } from 'vue'
import type { FileFactsSource, FileVersion } from '@/lib/fileFactsSource'

import { computed, ref, watch } from 'vue'

/** 消息时间往后放宽这么多：这段时间里存下的版本仍算「这条消息发的那一版」。 */
export const SAME_POST_MS = 10_000

/** 这条消息说的是哪一版：发出那一刻（放宽 SAME_POST_MS）之前存下的最后一版。 */
export function versionAtPost(versions: readonly FileVersion[], postedAt: string): FileVersion | null {
  const cutoff = Date.parse(postedAt) + SAME_POST_MS
  let best: FileVersion | null = null
  for (const v of versions) if (Date.parse(v.created_at) <= cutoff && (!best || v.seq > best.seq)) best = v
  return best
}

export function useFileFacts(
  facts: FileFactsSource,
  target: () => { topicId: string | null; path: string; postedAt?: string },
  seen: Ref<boolean>
) {
  const versions = ref<FileVersion[]>([])
  let generation = 0

  async function load() {
    const mine = ++generation
    versions.value = []
    const { topicId, path } = target()
    if (!seen.value || !topicId || !path) return
    try {
      const list = await facts.versions(topicId, path)
      if (mine === generation) versions.value = list
    } catch {
      // 取不到版本就不说版本：卡片照样能打开、能下载。
    }
  }

  watch([seen, () => target().topicId, () => target().path], load, { immediate: true })

  const latest = computed(() =>
    versions.value.reduce<FileVersion | null>((a, v) => (!a || v.seq > a.seq ? v : a), null)
  )
  const atPost = computed(() => {
    const postedAt = target().postedAt
    return postedAt ? versionAtPost(versions.value, postedAt) : null
  })
  /** 这条消息之后又存过：最新那一版比消息发的那一版新。 */
  const newer = computed(() => !!latest.value && !!atPost.value && latest.value.seq > atPost.value.seq)

  return { versions, latest, atPost, newer, reload: load }
}
