// 一段对话的章节：消息的 ⋯ 里「钉成章节」、概览里那一块列出来、点一条跳回去。
//
// 章节跟对话走：频道是频道它自己，任务是任务的 id（`conversationId`）。一段对话里
// 谁钉的章节大家都看得到；能不能钉由调用方判断（`enabled`），这里只管取和改。
import type { Block } from '../cx_types'

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { addChapter, removeChapter } from '../api/chapters'

import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { chaptersQuery } from '@/query/room'

export function useChapters(opts: {
  /** 这段对话的 id：频道本身，或房间里的一个任务。null = 这一栏没有章节。 */
  conversationId: () => string | null
  /** 出了错说一声（对话栏那条错误提示）。 */
  report: (message: string) => void
  /** 现在能不能改章节（没归档、在这里说得上话）。默认能。 */
  enabled?: () => boolean
}) {
  const read = useQuery(
    computed(() => {
      const id = opts.conversationId() ?? ''
      return { ...chaptersQuery(id), enabled: !!id && (opts.enabled?.() ?? true) }
    }),
    queryClient
  )
  const rows = computed(() => read.data.value ?? [])
  const chapterIds = computed<ReadonlySet<string>>(() => new Set(rows.value.map((row) => row.block.id)))

  async function reload() {
    const id = opts.conversationId()
    if (id) await queryClient.invalidateQueries({ queryKey: keys.roomChapters(id) })
  }

  async function act(run: () => Promise<unknown>, failed: string) {
    try {
      await run()
      await reload()
    } catch (e) {
      opts.report(e instanceof Error ? e.message : failed)
    }
  }

  function add(block: Block) {
    const id = opts.conversationId()
    if (id) void act(() => addChapter(id, block.id), t('work.room.chapter.failed'))
  }

  /** 取消章节：概览那一块按 id 来（那里手上是一个 row，不是被钉的那块消息），
   *  消息的 ⋯ 里按那一块来。 */
  function remove(target: Block | string) {
    const id = opts.conversationId()
    if (id)
      void act(() => removeChapter(id, typeof target === 'string' ? target : target.id), t('work.room.chapter.failed'))
  }

  return { rows, chapterIds, reload, add, remove }
}
