/**
 * 对话区那一册：对话栏里的一条消息（`RoomMessage`）、平台在房间里说的话
 * （`RoomNotice`）。
 *
 * 样例文字来自 `../catalogFixtures.ts`；「改了资料库文档」那一格用的平台事件在
 * `../catalogDocLine.ts`。
 */
import type { CatalogEntry } from './types'

import { LIBRARY_DOC_EDITED } from '../catalogDocLine'
import {
  ACCEPT_DONE,
  AGENT_NAME,
  ASK_ANSWERED,
  ASK_OPEN,
  CARD_FILED,
  CHEESE_LINES,
  LONG_ROW,
  ROOM_REFS,
  roomMessageProps,
  roomNoticeProps,
  TURN_SUMMARY,
  WANG_CLOSING,
  WANG_LINES,
} from '../catalogFixtures'

import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import { platformNotice } from '@/lib/platformNotice'

export const CONVERSATION_ENTRIES: CatalogEntry[] = [
  {
    id: 'room-message',
    title: 'RoomMessage',
    about: '对话栏里的一条消息：人说的、芝士说的、步骤清单、自己发的。',
    file: 'src/components/room/RoomMessage.vue',
    component: RoomMessage,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '提问：还没答（快捷回复）',
        note: '题就是一条消息，下面一排快捷回复；点一个就是回了那一句，也可以直接在输入框里回。',
        props: roomMessageProps(ASK_OPEN, { viewer: 'wang' }),
        expect: '课程平台收文件',
      },
      {
        name: '提问：已经有人答了（回执）',
        note: '有人答过之后，快捷回复收成「谁说了什么」。',
        props: roomMessageProps(ASK_ANSWERED),
        expect: '课程平台收文件',
      },
      {
        name: '留言（没有交给芝士）',
        note: '名字、头像、时间都带上的第一条（runStart）。',
        props: roomMessageProps(WANG_LINES[0]),
        expect: '先记一下：这个项目以后放课程资料',
      },
      {
        name: '点了名交给芝士',
        note: '<@handle> 渲染成可点的 chip，靠 refs 那张 handle→昵称的表。',
        props: roomMessageProps(WANG_LINES[1]),
        expect: '@芝士',
      },
      {
        name: '引用幻灯片页',
        note: '问题正文单独显示，资料可以展开核对原文与版本；资料中的 @ 名字照原文显示。',
        props: roomMessageProps(WANG_LINES[1], {
          block: {
            ...WANG_LINES[1].block,
            content: '<@cheese> 解释这一页',
            meta: {
              quoted_context: {
                kind: 'slide-page',
                path: 'slides/@评审 <@cheese-other>.pptx ',
                source: 'committed',
                version: 'v7',
                task_id: null,
                page: 2,
                text: '  @评审 <@cheese-other>\n本页保留原始空白和正文。\n',
              },
            },
          },
        }),
        expect: '引用第 2 页文字',
      },
      {
        name: '芝士的正文',
        note: '队友这一条挂的是它的身份（isAgent），头像按 handle 配色块。',
        props: roomMessageProps(CHEESE_LINES[0]),
        expect: '写完递验收卡给你',
      },
      ...(
        [
          ['think', '在想', '思考中', ' · 已用 6 秒'],
          ['work', '在干活', '正在运行命令', ' · 已用 41 秒'],
          ['stuck', '卡住了', '重试中（第 2 次）', ' · 已用 1 分 12 秒'],
          ['done', '做完了', null, ''],
        ] as const
      ).map(([face, label, faceStatus, elapsed]) => ({
        name: `芝士的头像：${label}`,
        note: '队友在干活时，对话里它最近出现的那个头像跟着状态动；在动时点它去现场。',
        props: roomMessageProps(CHEESE_LINES[0], {
          face,
          faceLabel: faceStatus && faceStatus + elapsed,
          faceStatus,
        }),
        expect: '写完递验收卡给你',
      })),
      {
        name: '步骤清单',
        note: '同一个块，正文换成清单：三项都打勾，末尾跟一句结果。',
        props: roomMessageProps(CHEESE_LINES[1]),
        expect: '读一下项目现有文件',
      },
      {
        name: '长正文',
        note: '一整段交代写进一条消息里：正文自己换行、自己撑高，行里别的格子不动。',
        props: roomMessageProps(LONG_ROW),
        expect: '我把这周的进度理了一遍',
      },
      {
        name: '自己发的：没送出去',
        note: '还没落库的那一条淡一档，形状不变；送失败时带「重试」和「编辑」。',
        props: roomMessageProps(WANG_CLOSING, { mine: true, outgoing: { failed: true } }),
        expect: '好了，谢谢',
      },
    ],
  },
  {
    id: 'room-notice',
    title: 'RoomNotice',
    about: '平台在房间里说的话：本轮摘要、验收卡的动静、谁采纳了。',
    file: 'src/components/room/RoomNotice.vue',
    component: RoomNotice,
    // 「去确认」那颗按钮是一条真链接（`NavLink`），所以路由还是要装：不装也画得出来，
    // 但那一颗会退化成一行点不动的字。Vuetify 是它自己那几颗图标要的。
    needs: ['vuetify', 'router'],
    states: [
      {
        name: '本轮摘要',
        note: '一轮里的动作行折成一行，改动摘要挂在后面（「改了 1 个文件（+6 -0）」）。',
        props: roomNoticeProps(TURN_SUMMARY),
        expect: '改动了 1 个文件',
      },
      {
        name: '验收卡已递出',
        note: '一条平台事件：写清是谁的改动、待谁审阅。',
        props: roomNoticeProps(CARD_FILED),
        expect: '待 王长鑫 审阅',
      },
      {
        name: '已采纳',
        note: '有人采纳、PR 合并：同一个组件，换的是事件本身。',
        props: roomNoticeProps(ACCEPT_DONE),
        expect: '采纳了这次改动',
      },
      {
        name: '改了资料库文档（在一轮里）',
        note: '一行淡字，不再写队友的名字；点这一行打开文档本身，「查看改动」打开时一处处标出改了什么。',
        props: {
          block: LIBRARY_DOC_EDITED,
          notice: platformNotice(LIBRARY_DOC_EDITED),
          run: [LIBRARY_DOC_EDITED],
          agent: { name: AGENT_NAME, handle: 'cheese' },
          time: '14:32',
          agentName: AGENT_NAME,
          refs: ROOM_REFS,
        },
        expect: '3 处',
      },
    ],
  },
]
