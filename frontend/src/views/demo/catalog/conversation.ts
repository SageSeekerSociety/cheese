/**
 * 对话区那一册：对话栏里的一条消息（`RoomMessage`）、平台在房间里说的话
 * （`RoomNotice`）、输入框上方那一行谁在忙（`MemberActivity`）。
 *
 * 样例文字来自 `../catalogFixtures.ts`；「改了资料库文档」那一格用的平台事件在
 * `../catalogDocLine.ts`。
 */
import type { Block } from '@/cx_types'
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

import MemberActivity from '@/components/room/MemberActivity.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import { platformNotice } from '@/lib/platformNotice'

/** 平台更新打断了一轮、又没能自己接着做时，这次的失败那一行（`turn_failed`）：改动都在，重试一次接着做。 */
const UPGRADE_TURN_FAILED: Block = {
  id: 'catalog-upgrade-turn-failed',
  conversation_id: 'demo',
  kind: 'event',
  author_type: 'platform',
  author: 'cheese',
  content: '平台更新打断了<@cheese>这次的工作，改好的文件都还在，重试一次就会接着做',
  turn_id: 'catalog-turn',
  created_at: new Date('2026-10-19T14:32:00+08:00').toISOString(),
  meta: {
    event_type: 'turn_failed',
    severity: 'warn',
    who: 'human',
    retryable: true,
    i18n: {
      content: { key: 'sessionReplacedForUpgrade', params: { agent: '<@cheese>' } },
    },
  },
}

/** AI 队友的运行程序正在下载到一个人自己的电脑上（`executor_install`）：同一行原地更新。 */
function installLine(state: string, key: string, content: string, params: Record<string, unknown>): Block {
  return {
    id: `catalog-executor-install-${state}`,
    conversation_id: 'demo',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content,
    created_at: new Date('2026-10-23T10:05:00+08:00').toISOString(),
    meta: {
      event_type: 'executor_install',
      severity: state === 'stopped' ? 'warn' : 'info',
      who: state === 'downloading' ? 'platform' : '',
      state,
      i18n: { content: { key, params } },
    },
  }
}

const INSTALL_DOWNLOADING = installLine(
  'downloading',
  'executorInstallDownloading',
  '正在为「alice 的 MacBook」安装 AI 队友的运行程序：已下载 120 / 240 MB',
  { device: 'alice 的 MacBook', done: 120, total: 240 }
)
const INSTALL_DONE = installLine(
  'installed',
  'executorInstallDone',
  '已在「alice 的 MacBook」上安装 AI 队友的运行程序',
  { device: 'alice 的 MacBook' }
)
const INSTALL_STOPPED = installLine(
  'stopped',
  'executorInstallStopped',
  '为「alice 的 MacBook」安装 AI 队友的运行程序未完成：已下载 120 / 240 MB，AI 队友下次用这台电脑时接着下载',
  { device: 'alice 的 MacBook', done: 120, total: 240 }
)

function installNotice(block: Block): Record<string, unknown> {
  return {
    block,
    notice: platformNotice(block),
    run: [block],
    agent: null,
    time: '10:05',
    agentName: AGENT_NAME,
    refs: ROOM_REFS,
  }
}

function upgradeNotice(block: Block): Record<string, unknown> {
  return {
    block,
    notice: platformNotice(block),
    run: [block],
    agent: { name: AGENT_NAME, handle: 'cheese' },
    time: '14:32',
    agentName: AGENT_NAME,
    refs: ROOM_REFS,
  }
}

/** 一轮没做完时平台替队友说的那一行（`turn_failed` / `turn_timeout`，后端那张表看成 `for_agent`）。 */
function failureLine(id: string, content: string, meta: Record<string, unknown>): Block {
  return {
    id: `catalog-failure-${id}`,
    conversation_id: 'demo',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese',
    content,
    turn_id: 'catalog-turn',
    created_at: new Date('2026-11-01T14:32:00+08:00').toISOString(),
    shown_as: 'for_agent',
    meta: { event_type: 'turn_failed', severity: 'error', who: 'human', ...meta },
  }
}

const OVERLOADED_WORDS = 'API Error: 529 Overloaded. This is a server-side issue, usually temporary.'

const MODEL_ERROR = failureLine('model-error', '这次没做完：调用的模型出错了', {
  retryable: true,
  code: 'model_error',
  detail: OVERLOADED_WORDS,
  detail_label: '服务原话',
  i18n: {
    content: { key: 'turnFailedModelError', params: {} },
    detail_label: { key: 'labelServiceWords', params: {} },
  },
})

const OWN_BALANCE = failureLine(
  'own-balance',
  '这次没做完：你们接入的 DeepSeek（项目设置里的「模型连接」）余额用完了。充值后点「重试」，或者换一个模型连接。',
  {
    retryable: true,
    code: 'own_out_of_credit',
    detail: '{"error":{"message":"Insufficient Balance","code":"invalid_request_error"}}',
    detail_label: '服务原话',
    i18n: {
      content: { key: 'turnFailedOwnBalance', params: { service: 'DeepSeek' } },
      detail_label: { key: 'labelServiceWords', params: {} },
    },
  }
)

const REQUEST_REFUSED = failureLine('refused', '这次没做完', {
  detail:
    '模型服务把这次请求当作无效请求拒收了，多半是对话里有它不接受的内容，比如一张解不开的图片。重试会把同样的内容再发一遍，结果一样；需要管理员处理。',
  detail_label: '详细说明',
  i18n: {
    content: { key: 'turnFailedService', params: {} },
    detail_label: { key: 'labelDetails', params: {} },
  },
})

const TURN_STOPPED = failureLine('stopped', '这次没做完，已停下', {
  event_type: 'turn_timeout',
  severity: 'warn',
  retryable: true,
  i18n: { content: { key: 'turnStopped', params: {} } },
})

function failureNotice(block: Block): Record<string, unknown> {
  return {
    block,
    notice: platformNotice(block),
    run: [block],
    agent: { name: AGENT_NAME, handle: 'cheese' },
    time: '14:32',
    agentName: AGENT_NAME,
    refs: ROOM_REFS,
    canRetry: true,
    projectId: 'demo-project',
  }
}

/** 输入框上方那一行里的一位队友：`minutes` 分钟前开始（用时从它算）。 */
function activityLine(
  handle: string,
  name: string,
  kind: 'working' | 'retrying',
  minutes: number,
  step?: string
): Record<string, unknown> {
  return { handle, name, kind, since: Math.floor(Date.now() / 1000) - minutes * 60, step: step ?? null }
}

const CHEESE_WORKING = activityLine('cheese', '芝士', 'working', 2, '执行命令 pnpm test')
const SPARK_RETRYING = activityLine('spark', '火花', 'retrying', 3)

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
        name: '他的芝士替他发的',
        note: '署名照旧是他；名字旁边标「芝士代发」（meta.by_assistant），悬停说是谁的芝士。',
        props: roomMessageProps(WANG_LINES[0], {
          block: { ...WANG_LINES[0].block, meta: { ...WANG_LINES[0].block.meta, by_assistant: true } },
        }),
        expect: '芝士代发',
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
        expect: '修改了 1 个文件',
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
      {
        name: '平台更新：没能自己接着做',
        note: '平台更新打断的那一轮通常几秒内自己接着做，对话里不落字；没能接着做时，这一行说改动都还在，给一个「重试」。',
        props: upgradeNotice(UPGRADE_TURN_FAILED),
        expect: '平台更新打断了',
      },
      {
        name: '没做完：调用的模型出错了',
        note: '模型那一侧的失败（自动重试用完、要很久才恢复、平台的额度不足、过载）都是这一句，带「重试」；服务原话收在展开里。',
        props: failureNotice(MODEL_ERROR),
        expect: '调用的模型出错了',
      },
      {
        name: '没做完：接入的模型连接没钱了',
        note: '项目自己接入的模型连接余额用完：说出是哪一个、去哪里换，带「重试」和「去模型连接」。',
        props: failureNotice(OWN_BALANCE),
        expect: '你们接入的 DeepSeek',
      },
      {
        name: '没做完：请求被模型拒收',
        note: '模型把这次请求当作无效请求拒收：照旧，不给「重试」。',
        props: failureNotice(REQUEST_REFUSED),
        expect: '这次没做完',
        expectSelector: '[data-testid="platform-notice"]',
      },
      {
        name: '没做完，已停下',
        note: '一轮很久没有任何输出、平台把它停下了：这是平台的问题，不说模型出错；带「重试」，同时告警给运维。',
        props: failureNotice(TURN_STOPPED),
        expect: '这次没做完，已停下',
      },
      {
        name: '运行程序下载中',
        note: 'AI 队友第一次在一个人自己的电脑上干活时，下载运行程序的进度：同一行原地更新，不另起新行。',
        props: installNotice(INSTALL_DOWNLOADING),
        expect: '已下载 120 / 240 MB',
      },
      {
        name: '运行程序已装好',
        note: '下载并装好之后，同一行改成这一句。',
        props: installNotice(INSTALL_DONE),
        expect: '安装 AI 队友的运行程序',
      },
      {
        name: '运行程序没下完',
        note: '这一次在时限里没下完：说下到哪了，下次接着下，同一行到时再接着更新。',
        props: installNotice(INSTALL_STOPPED),
        expect: '接着下载',
      },
    ],
  },
  {
    id: 'member-activity',
    title: 'MemberActivity',
    about: '输入框上方那一行：谁在打字、哪位 AI 队友在干活、哪位调用模型出错了在等平台自动重试。',
    file: 'src/components/room/MemberActivity.vue',
    component: MemberActivity,
    needs: ['i18n'],
    states: [
      {
        name: '一位在干活',
        note: '绿点，「谁正在工作… · 此刻那一步 · 用时」。',
        props: { lines: [CHEESE_WORKING] },
        expect: '执行命令 pnpm test',
      },
      {
        name: '一位在等自动重试',
        note: '同一行、同一个位置和字号，只换成黄点和这一句；不说几点重试，用时从出错时算。',
        props: { lines: [SPARK_RETRYING] },
        expect: '火花调用的模型出错了，正在自动重试',
      },
      {
        name: '两位：一位在干活，一位在等',
        note: '各占一行，上下对齐，只差开头那颗点和那句话。',
        props: { lines: [CHEESE_WORKING, SPARK_RETRYING] },
        expect: '芝士正在工作',
      },
    ],
  },
]
