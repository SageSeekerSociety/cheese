// 任务状态的呈现规则 —— **这里没有一行在算状态**。
//
// 一件任务落哪一格、写哪句话，全部由后端算好放在 `presentation` 里。前端这边只留下
// 和后端无关的几件事：格子叫什么、那句话怎么说、色点长什么样。
//
// 前端不再自己推一遍，是因为两个算法算同一个东西必然会走散，而屏幕上那个词到底是
// 哪一个算出来的，看的人分辨不了——他只会得出「界面在骗我」这一个结论。
//
// 格子的判据是「**该谁动**」，不是「事情进行到哪一步」。同一个客观事实——比如快检
// 红了——下一步在平台手上就落 `delivering`，在人手上就落 `needs_you`。

import type { BoardColumn, BoardPhrase, RoomTask } from '@/cx_types'

import i18n, { t } from '@/i18n'

// 列名按当前语言现取，不存成常量：切换语言后要跟着变。
export function columnLabel(column: BoardColumn): string {
  return t(`work.board.column.${column}`)
}

/** 卡面上那一句，按当前语言现取。后端给的是码（`presentation.phrase`）；这一版
 *  不认识的码（后端先发了新短语）照原样写出来，不写成空白。 */
export function phraseLabel(phrase: BoardPhrase): string {
  return i18n.global.te(`work.board.phrase.${phrase}`, 'zh-CN') ? t(`work.board.phrase.${phrase}`) : phrase
}

/** 等的是**看的这个人**时卡面那一句：「待审阅」对他说成「待你审阅」，「讨论中」说成
 *  「待你开始」—— 文档写好了，下一步是他点开始。没有专门说法的照通用那一句。 */
export function myPhraseLabel(phrase: BoardPhrase): string {
  return i18n.global.te(`work.board.mine.${phrase}`, 'zh-CN') ? t(`work.board.mine.${phrase}`) : phraseLabel(phrase)
}

/** 一件任务的那一句，按看的人说：在等的就是他，用 `myPhraseLabel`。页头、频道概览
 *  和频道里的卡说的是同一件事，对同一个人不能一处「讨论中」一处「待你开始」。 */
export function taskPhraseLabel(task: Pick<RoomTask, 'presentation' | 'waiting_on'>, viewer: string): string {
  const { phrase } = task.presentation
  return task.waiting_on && task.waiting_on === viewer ? myPhraseLabel(phrase) : phraseLabel(phrase)
}

/** 色点长什么样。
 *
 *  它返回内联样式而不是让三个组件各写一遍 CSS：`scoped` 的样式进不了别的组件，所以
 *  「同一套列色」在 CSS 里只能靠复制三份来实现，而复制三份的意思就是迟早只改其中
 *  一份。同一个理由，颜色一律是 token 不是字面量——写死的颜色在两个主题里必然错
 *  一个。
 *
 *  只有 `needs_you` 是暖色且实心：整块板上唯一需要人动手的那一列，应该是唯一抓眼
 *  睛的。其余靠形状分（空心 / 虚线 / 实心），所以把颜色关掉也还读得出来。 */
export function columnDotStyle(column: BoardColumn): Record<string, string> {
  if (column === 'not_started') return { borderColor: 'var(--faint)', borderStyle: 'dashed' }
  if (column === 'building') return { borderColor: 'var(--ok)' }
  if (column === 'delivering') return { borderColor: 'var(--ok)', borderStyle: 'dashed' }
  if (column === 'needs_you') return { borderColor: 'var(--warn)', background: 'var(--warn)' }
  if (column === 'done') return { borderColor: 'var(--faint)', background: 'var(--faint)' }
  return { borderColor: 'var(--line-2)' }
}

/** 还有人要管的那些任务：去掉已归档频道里没走完的。
 *
 *  任务不归档，频道归档：一个频道收了尾，里面没走完的任务后端照样按它自己的状态落在
 *  未开始 / 进行中 / 检查中 / 待处理里，可频道归档了就没人会再去动它们——全部任务答的
 *  是「接下来谁要动什么」，它们不该在上面。已完成不筛：那是交付过的东西。 */
export function liveTasks<T extends { room_id: string; presentation: { column: BoardColumn } }>(
  tasks: readonly T[],
  archivedRoomIds: ReadonlySet<string>
): T[] {
  return tasks.filter((task) => task.presentation.column === 'done' || !archivedRoomIds.has(task.room_id))
}
