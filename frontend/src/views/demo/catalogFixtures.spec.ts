/**
 * `./catalogFixtures` 这道出口的公开面。
 *
 * 夹具拆进 `./catalogFixtures/` 之后，这道出口是唯一还必须和以前一字不差的东西：
 * 三十来个文件从它取数据（`catalogAccept.ts`、`catalog/` 下面几册、各面板自己的
 * spec……），少重发一个名字、或者顺手多发出一个，别的检查都不响 —— 少一个只有
 * 正好用它的那一个文件才红，多一个没人红。所以这里钉的是「出口上有什么」，
 * 不是里面怎么分的。
 */
import { expect, it } from 'vitest'

import * as fixtures from './catalogFixtures'

it('出口上就是拆分前那 62 个值', () => {
  // 拆分之前这份模块也只导出这些：`RoomRow` 是类型，运行时不存在，所以不在这一列。
  expect(Object.keys(fixtures).sort()).toEqual([
    'ACCEPT_APPROVALS',
    'ACCEPT_BLOCKED',
    'ACCEPT_CARD',
    'ACCEPT_CHECKS',
    'ACCEPT_CONFLICTED',
    'ACCEPT_DELIVERING_ONE',
    'ACCEPT_DONE',
    'ACCEPT_GATE_BLOCKED',
    'ACCEPT_GATE_FAILED',
    'ACCEPT_NOTE_ERROR',
    'ACCEPT_NOTE_INFO',
    'ACCEPT_ONE',
    'ACCEPT_REVIEWERS',
    'ACTION_ROWS',
    'ADMIN_QUEUE',
    'AGENT_NAME',
    'ASK_ANSWERED',
    'ASK_OPEN',
    'BAR_ROWS',
    'BAR_ROWS_LONG',
    'CARD_FILED',
    'CHANGES_EMPTY',
    'CHAT_ROWS',
    'CHAT_ROWS_FULL',
    'CHAT_TOPIC',
    'CHEESE_LINES',
    'COMPUTE_DEVICES',
    'DOC_TOPIC',
    'EXCERPTS',
    'FEEDBACK_ROWS',
    'KPI_STATES',
    'LONG_ROW',
    'NAV_ITEMS',
    'NOTHING_CHANGED',
    'NO_REPO',
    'NUMBER_ROWS',
    'OPEN_FILES',
    'PREVIEW_EMPTY',
    'RAIL_ACTIONS',
    'RAIL_MARKS',
    'RAIL_PAGES',
    'RAIL_ROOT_TOPIC',
    'RAIL_ROWS',
    'RAIL_TERMS',
    'ROOM_MENTION_POOL',
    'ROOM_REFS',
    'SHEET_ACTIONS',
    'TURN_SUMMARY',
    'WANG_CLOSING',
    'WANG_EDITING_ROW',
    'WANG_LINES',
    'acceptChecks',
    'acceptHeadProps',
    'changesPanelProps',
    'chatTimelineProps',
    'docPanelProps',
    'docSession',
    'installCatalogAnswers',
    'isMineWang',
    'previewPanelProps',
    'roomMessageProps',
    'roomNoticeProps',
  ])
})

it('每一册都真的接在出口上', () => {
  // 名字重发了、背后却是空的，是这套拆法最像真的的坏法（`./catalogFixtures/` 下
  // 某一册被搬走后留了个空壳）。每册挑一条**跨册**的关系来钉：它同时用到了出口上
  // 两个名字，读到的那一个是另一个给的，就不是各自独立的一份数据。
  expect(fixtures.roomMessageProps(fixtures.WANG_LINES[0]).block).toBe(fixtures.WANG_LINES[0].block) // room 拿房间行翻 props
  expect(fixtures.acceptHeadProps().card).toBe(fixtures.ACCEPT_ONE) // accept 的顶部那块拿的正是那张卡
  expect(fixtures.KPI_STATES.linked.to).toBe(fixtures.ADMIN_QUEUE) // dashboard 里的去处是它自己那条
  expect(fixtures.RAIL_MARKS.working[0]?.name).toBe(fixtures.AGENT_NAME) // rail 的名字来自剧本那位队友
  expect(fixtures.docPanelProps().topic).toBe(fixtures.DOC_TOPIC) // panels 的文档格认的是同一个话题
  expect(fixtures.chatTimelineProps().rows).toBe(fixtures.CHAT_ROWS) // chat 的日期线是拿这几行算的
  // 剩下三册（navigation、feedback、common）的名字彼此不互指，只能钉它们没变空：
  // 空掉的那一天，用它们的页面画出来就是一片空的，而这几个断言先红。
  expect(fixtures.NAV_ITEMS.length).toBeGreaterThan(0)
  expect(fixtures.SHEET_ACTIONS.length).toBeGreaterThan(0)
  expect(fixtures.OPEN_FILES.length).toBeGreaterThan(0)
  expect(Object.keys(fixtures.FEEDBACK_ROWS).length).toBeGreaterThan(0)
  expect(fixtures.EXCERPTS.length).toBeGreaterThan(0)
  expect(fixtures.ROOM_REFS.mentionNames).toEqual(expect.objectContaining({ wang: expect.any(String) }))
  expect(fixtures.CHAT_ROWS_FULL.length).toBeGreaterThan(0)
})
