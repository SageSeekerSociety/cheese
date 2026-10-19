/**
 * 夹具的地基：剧本那一刻（`SCENE`、`NAMES`、`ROOM_REFS`、`AGENT_NAME`），以及把
 * 一帧的行折成 `RoomRow` 的三条管线（`rows` / `messagesOf` / `noticesOf`）。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：原来那份文件 989 行，顶上就是 1000 行
 * 的仓库上限，按数据域分成 `./` 下面这几册。这一册里比原来多出来的只有给人用的
 * `export`（原来它们只在这一册内部用），代码本身一字未改。
 */
import type { Block } from '@/cx_types'
import type { ChatLine, Frame } from '../demoScene'

import { frameAt } from '../demoScene'
import { SCENES } from '../scenes'

import { collapseNotices, type PlatformNotice } from '@/lib/platformNotice'

export const SCENE = SCENES.quickstart

/** 剧本放到第 step 步末尾的那一帧：`elapsed` 给一个够大的数，这一步的事都演完了。 */
export function frame(step: number): Frame {
  return frameAt(SCENE, step, Number.MAX_SAFE_INTEGER)
}

export const NAMES: Record<string, string> = Object.fromEntries(
  Object.entries(SCENE.people).map(([handle, person]) => [handle, person.name])
)

/** 正文里的 token 靠它渲染成可点的 chip（和 `DemoRoom` 给的是同一份）。 */
export const ROOM_REFS = { mentionNames: NAMES, topicTitles: {} }

/** 房间当前那位 AI 队友的名字（剧本里第一个座位，`DemoRoom` 也是这么算的）。 */
export const AGENT_NAME = NAMES[SCENE.seats?.[0] ?? ''] ?? '芝士'

export interface RoomRow {
  line: ChatLine
  block: Block
  notice: PlatformNotice | null
  run: Block[]
}

/** 时间线上的行。和 `DemoRoom.rows` 同一条管线，只是不需要它那两种不是块的行
 *  （`mark` 分隔说明、`split` 已派出标记）——预览站没有整条时间线要摆。 */
export function rows(step: number): RoomRow[] {
  const chat = frame(step).chat
  const byId = new Map(chat.filter((l) => l.block).map((l) => [l.block!.id, l]))
  return collapseNotices(chat.filter((l) => l.block).map((l) => l.block!)).map((row) => ({
    line: byId.get(row.block.id)!,
    block: row.block,
    notice: row.notice,
    run: row.run,
  }))
}

/** 第 step 步里某一位说的那几条消息。 */
export function messagesOf(step: number, who: string): RoomRow[] {
  return rows(step).filter((r) => !r.notice && r.block.kind === 'message' && r.block.author === who)
}

/** 第 step 步里的通知（折过之后还是通知的那些行）。 */
export function noticesOf(step: number): RoomRow[] {
  return rows(step).filter((r) => r.notice)
}

/** 某一步里最后出现的那一条：`frameAt` 是从第一步重放到这一步的，所以第 N 步的
 *  「那一条」总是它的最后一条，不是整个房间的第一条。 */
export function latest<T>(list: T[]): T {
  return list[list.length - 1]
}
