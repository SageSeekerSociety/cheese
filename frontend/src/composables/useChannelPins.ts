// 对话栏里置顶那件事：频道主线上哪几条已经置顶（它们的 ⋯ 里是「取消置顶」、旁边带
// 小图钉），以及置顶、取消置顶、把芝士摆出来的东西存进资料库这三个动作。
//
// 什么时候读：时间线上出现「谁置顶了什么」那一行时，以及频道说它的置顶变了（`pins`）
// 时。置顶那一行总在被置顶的那一条之后，所以屏幕上有一条置顶了的消息，它那一行也一定
// 在屏幕上；一行都没有的时候不必为一份空名单多问一次。
import type { Block } from '../cx_types'

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { saveRoomOutputToLibrary } from '../api'
import { pinBlock, unpinBlock } from '../api/pins'
import { queryClient } from '../lib/queryClient'
import { keys } from '../queries/keys'
import { pinsQuery } from '../queries/room'

import { t } from '@/i18n'

export function useChannelPins(opts: {
  /** 这一栏是频道主线时是频道 id；否则 null，什么都不读。 */
  channelId: () => string | null
  /** 出了错说一声（对话栏那条错误提示）。 */
  report: (message: string) => void
  /** 时间线上有「谁置顶了什么」那一行。 */
  seen: () => boolean
}) {
  // 和频道概览里那一块读同一份（`queries/room`）。
  const read = useQuery(
    computed(() => {
      const id = opts.channelId() ?? ''
      return { ...pinsQuery(id), enabled: !!id && opts.seen() }
    })
  )
  // 小图钉是装饰：读不到就先不画，下次频道说置顶变了时再读。
  const pinnedIds = computed<ReadonlySet<string>>(() => new Set((read.data.value ?? []).map((p) => p.block.id)))
  async function reload() {
    const id = opts.channelId()
    if (id) await queryClient.invalidateQueries({ queryKey: keys.roomPins(id) })
  }

  async function act(run: () => Promise<unknown>, failed: string) {
    try {
      await run()
      await reload()
    } catch (e) {
      opts.report(e instanceof Error ? e.message : failed)
    }
  }
  function pin(block: Block) {
    const id = opts.channelId()
    if (id) void act(() => pinBlock(id, block.id), t('work.room.pin.failed'))
  }
  function unpin(block: Block) {
    const id = opts.channelId()
    if (id) void act(() => unpinBlock(id, block.id), t('work.room.pin.failed'))
  }
  /** 芝士摆出来的一份东西存进资料库。 */
  function keep(roomId: string, block: Block) {
    void act(() => saveRoomOutputToLibrary(roomId, block.content), t('work.room.file.keepFailed'))
  }

  return { pinnedIds, reload, pin, unpin, keep }
}
