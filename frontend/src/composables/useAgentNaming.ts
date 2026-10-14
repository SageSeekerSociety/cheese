// 这一栏此刻说给谁听、界面上怎么称呼它。
//
// 座位有两个来源：房间名册上那位（名册上没有就用项目默认那位，见 useRoomRoster），
// 和任务单独指定的那一位——负责人能在任务信息卡那一行把这件事交给另一位队友。两个
// 来源都由调用方传进来，这里只负责算出一个座位、给它的名字、输入框那行提示语，并把
// `<@handle>` 渲染用的 handle→名字那张表和名册对齐。
import type { MentionIdentity } from '@/lib/refChip'

import { computed, type ComputedRef, watch } from 'vue'

import { t } from '@/i18n'

type Seat = { handle: string; label: string }

/** 名册上能 @ 到的一位。头像、是不是队友跟着名字一起带上，`@` chip 前那张脸要用。 */
type PoolEntry = { handle: string; label: string; avatar?: string | null; agent?: boolean }

export function useAgentNaming(opts: {
  /** 房间的座位：名册上那位，名册上没有就用项目默认那位。名册还没到时为空。 */
  roomSeat: ComputedRef<Seat | null>
  /** 名册上能 @ 到的人：任务指定那位要看它在不在册。 */
  pool: ComputedRef<PoolEntry[]>
  /** handle 翻成界面上的名字；不在册上时给 null。 */
  nameOf: (handle: string) => string | null
  /** `<@handle>` 渲染用的表；本模块负责把它和名册对齐。 */
  names: Record<string, string>
  /** handle → 头像与「是不是队友」，和 `names` 一起对齐；`@` chip 前面那张脸用它。 */
  identities?: Record<string, MentionIdentity>
  /** 这段对话说给谁听，当它不是房间那位时的 handle：任务单独指定的队友，或支线正在
   *  和它说话的那位；空＝跟着房间那位。 */
  agentHandle?: () => string | null
  alwaysSummon: () => boolean
}) {
  // 房间里是房间那位，任务里是**这件事**那位，支线里是它正在和谁说话的那位——换过之后
  // @ 就得写它：正文里 @ 的是谁，读的人就以为叫的是谁，而任务选的、支线叫来的那位和
  // 房间那位常常不是同一个。挑的那位一定在这间
  // 房的名册上（后端换的时候只认名册上那个座位），名字从名册上取；名册还没到、或者它
  // 已经不在名册上了，就不猜，退回房间那位——写一个点不动的 @ 比不写更糟。
  const agentSeat = computed<Seat | null>(() => {
    const handle = opts.agentHandle?.() ?? null
    const label = handle ? opts.nameOf(handle) : null
    return handle && label ? { handle, label } : opts.roomSeat.value ?? null
  })
  /** 界面上称呼它用的名字。名册没到时谁也不猜，就写「芝士」。 */
  const agentName = computed(() => agentSeat.value?.label || t('work.room.defaultAgentName'))

  // 输入框那一行提示语。和芝士私聊时它**不能**说「交给它做」：私聊不占机器，那边的
  // 芝士没有工具，读不了文件也跑不了命令。一句承诺它做不到的事的提示语，换来的是一
  // 次「我试了但做不了」，而人只会记得是它没做成。
  const composerHint = computed(() =>
    opts.alwaysSummon()
      ? t('work.room.composer.placeholderDm', { name: agentName.value })
      : t('work.room.composer.placeholder', { name: agentName.value })
  )

  // Keep the handle→name map in sync with the roster, so <@handle> tokens render
  // with the member's display name. Only what changed is written: every message body
  // watches this map, and refilling it redraws the whole timeline on each roster read.
  watch(
    opts.pool,
    (pool) => {
      const next = new Map(pool.map((row) => [row.handle, row.label]))
      // 群播 tokens (fusion-design §3): <@all>/<@here> render as friendly chips,
      // not the raw literal — they are reserved handles, not roster members.
      next.set('all', t('work.room.mention.all'))
      next.set('here', t('work.room.mention.here'))
      for (const k of Object.keys(opts.names)) if (!next.has(k)) delete opts.names[k]
      for (const [k, label] of next) if (opts.names[k] !== label) opts.names[k] = label
      // 头像那张小脸跟着名字对齐，一份名册两个表：正文里的 @ 和名册上那位长得一样。
      // all/here 是群播、不是人，不给脸。和上面 `names` 同一个道理：只写变了的，名册原地
      // 重读一遍不该把整条时间线重画一次。
      if (opts.identities) {
        const known = new Set(pool.map((row) => row.handle))
        for (const k of Object.keys(opts.identities)) if (!known.has(k)) delete opts.identities[k]
        for (const row of pool) {
          const face = { avatar: row.avatar ?? null, agent: !!row.agent }
          const prev = opts.identities[row.handle]
          if (!prev || prev.avatar !== face.avatar || prev.agent !== face.agent) opts.identities[row.handle] = face
        }
      }
    },
    { immediate: true, deep: true }
  )

  return { agentSeat, agentName, composerHint }
}
