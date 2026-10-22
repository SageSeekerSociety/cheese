// 一条平台事件长什么样：把 `meta` 里的结构化载荷读出来，判成哪一种卡。
//
// 载荷是后端按契约写进 `meta` 的（契约见 platformNotice.ts 的模块注释）：本轮改动摘要
// (`changeset`)、邮件草稿 (`mail` 和后来的 `mail_result`)、文档那一行的改动
// (`document` / `doc_edits` / `doc_suggestions`)、以及「去确认」要指到哪一条
// (`routine_id` / `skill_id`)。这里只读后端已经发出来的东西，读不到就是没有。
//
// 判档的顺序即优先级（事故卡 > 动作行 > 草稿卡 > 折叠行 > 淡行），和改动前的模板一致。
import type { Block } from '../cx_types'
import type { DocEdit } from './docEdits'
import type {
  ChangeSummary,
  ConfirmTarget,
  EnvironmentTarget,
  LibraryDocCard,
  MailDraftView,
  MailOutcome,
  NoticeOccurrence,
  PlatformNotice,
  WhoTag,
} from './platformNotice'

import { noticeText } from './noticeText'
import { platformErrorPresentation } from './platformEvents'

import { t } from '@/i18n'

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function meta(block: Block): Record<string, unknown> | null {
  return (block.meta as Record<string, unknown> | null) ?? null
}

// Catalog keys, looked up when a row is built so a language switch re-reads them.
const WHO_LABEL: Record<WhoTag, string> = {
  platform: 'work.room.notice.who.platform',
  cheese: 'work.room.notice.who.cheese',
  human: 'work.room.notice.who.human',
}

export function whoTag(block: Block): WhoTag | '' {
  const who = str(meta(block)?.who)
  return who in WHO_LABEL ? (who as WhoTag) : ''
}

function whoLabel(block: Block): string {
  const who = whoTag(block)
  return who ? t(WHO_LABEL[who]) : ''
}

/** detail 是一个人写的一句话（不是日志）的事件：这句话直接跟在那一行后面。 */
const QUOTED_DETAIL = new Set(['card_rejected'])

/**
 * meta.action（结构化）优先，老的 `action:<resource>` ref 仍然认。
 * 从 ChatPanel 搬过来，行为一字不改。
 */
export function actionResource(block: Block): string | null {
  const metaAction = meta(block)?.action
  if (typeof metaAction === 'string') return metaAction
  const r = (block.refs || []).find((x) => x.startsWith('action:'))
  return r ? r.slice('action:'.length) : null
}

export function changeSummary(block: Block): ChangeSummary | null {
  const raw = meta(block)?.changeset
  if (!raw || typeof raw !== 'object') return null
  const c = raw as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
  return {
    filesTotal: num(c.files_total),
    added: num(c.added),
    removed: num(c.removed),
    files: Array.isArray(c.files)
      ? c.files
          .map((f) => {
            const file = (f ?? {}) as Record<string, unknown>
            return { path: String(file.path ?? ''), added: num(file.added), removed: num(file.removed) }
          })
          .filter((f) => f.path)
      : [],
    filesOmitted: num(c.files_omitted),
  }
}

function mailDraftView(block: Block): MailDraftView | null {
  const m = meta(block)
  if (str(m?.event_type) !== 'mail_drafted') return null
  const mail = m?.mail as Record<string, unknown> | undefined
  const draftId = str(m?.mail_draft_id)
  if (!mail || !draftId) return null
  const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : [])
  return {
    draftId,
    owner: str(mail.owner),
    draftedBy: str(mail.drafted_by),
    account: str(mail.account),
    to: list(mail.to),
    cc: list(mail.cc),
    subject: str(mail.subject),
    body: str(mail.body),
    attachments: Array.isArray(mail.attachments)
      ? mail.attachments.map((a) => {
          const item = a as Record<string, unknown>
          return { name: str(item.name), size: typeof item.size === 'number' ? item.size : null }
        })
      : [],
  }
}

/** 房间里每封草稿的下落：draft id → 最后一条结果。 */
export function mailOutcomes(blocks: Block[]): Map<string, MailOutcome> {
  const found = new Map<string, MailOutcome>()
  for (const block of blocks) {
    const m = meta(block)
    if (block.kind !== 'event' || str(m?.event_type) !== 'mail_result') continue
    const status = str(m?.status)
    if (status !== 'sent' && status !== 'failed' && status !== 'discarded') continue
    found.set(str(m?.mail_draft_id), {
      status,
      sentAt: str(m?.sent_at) || null,
      reason: str(m?.detail) || null,
    })
  }
  return found
}

function docEdits(raw: unknown): DocEdit[] {
  return Array.isArray(raw)
    ? raw.flatMap((e: unknown) => {
        const item = e as { old?: unknown; new?: unknown } | null
        return typeof item?.old === 'string' && typeof item?.new === 'string' ? [{ old: item.old, new: item.new }] : []
      })
    : []
}

/** 文档那一行带的「改了哪几处」和「提了几处建议」（后端写在 meta 里）。 */
function docChange(block: Block): {
  docEdits?: DocEdit[]
  docSuggestions?: number
  libraryDoc?: LibraryDocCard
} {
  const m = meta(block)
  if (!m) return {}
  const doc = m.document as { id?: unknown; title?: unknown } | undefined
  if (doc && typeof doc.id === 'string') {
    return {
      libraryDoc: {
        id: doc.id,
        title: typeof doc.title === 'string' ? doc.title : '',
        created: m.doc_created === true,
        edits: docEdits(m.doc_edits),
        suggestions: m.doc_suggested === true && Array.isArray(m.doc_suggestions) ? m.doc_suggestions.length : 0,
      },
    }
  }
  if (m.doc_suggested === true) {
    const ids = Array.isArray(m.doc_suggestions) ? m.doc_suggestions.filter((id) => typeof id === 'string') : []
    return { docSuggestions: ids.length }
  }
  const edits = docEdits(m.doc_edits)
  return edits.length ? { docEdits: edits } : {}
}

/** meta.action 事件的 content 自带主语（张衡/芝士 编辑了文档）；老卡片要补「芝士」。 */
function actionText(block: Block): string {
  return typeof meta(block)?.action === 'string'
    ? noticeText(block)
    : t('topic.notice.legacyAction', { action: block.content })
}

/** 卡面上留一句就够了，超过这个长度就切断 —— 切掉的部分原样进展开区，不丢。 */
const LEAD_MAX = 60

/**
 * 取第一句当可见的那一行，其余全部收起来。
 *
 * 中文句号和换行都算断句；一句话本身就超长时按 LEAD_MAX 硬切 —— 卡面高度是产品
 * 约束（≤2-3 行），不能交给内容长度决定。
 *
 * 中文的 。！？ 后面不跟空格，所以它们单独就算断句；英文的 . ! ? 要后跟空白或
 * 结尾才算，否则 "PR #1.2 没过" 会被从小数点上切开。
 */
function splitLead(text: string): { lead: string; rest: string } {
  const body = text.trim()
  if (!body) return { lead: '', rest: '' }

  const match = /[。！？]|\n|[.!?](?=\s|$)/.exec(body)
  let cut = match ? match.index + (body[match.index] === '\n' ? 0 : 1) : body.length
  let ellipsis = false
  if (cut > LEAD_MAX) {
    cut = LEAD_MAX
    ellipsis = true
  }

  const lead = body.slice(0, cut).trim()
  const rest = body.slice(cut).trim()
  return { lead: ellipsis ? `${lead}…` : lead, rest }
}

function occurrenceOf(block: Block): NoticeOccurrence {
  return {
    line: noticeText(block),
    label: noticeText(block, 'detail_label'),
    detail: noticeText(block, 'detail'),
  }
}

/**
 * 「芝士整理了技能」那一行要带一颗「去确认」。
 *
 * 确认入口在项目名旁的 ⋯ 菜单里，人在房间读到这一行时不知道去哪找（chiruotong
 * 在 dev 上实测找不到）。这一行本身就知道是哪一条，所以直接指过去。
 */
export function confirmTarget(block: Block, projectId: string | null | undefined): ConfirmTarget | null {
  const m = meta(block)
  if (!projectId) return null
  // 人已经定下来了（保存、拒绝、删除）：这一行只说结果，没有要去确认的了。
  if (str(m?.state) === 'over') return null
  if (str(m?.event_type) === 'skill_proposed' && str(m?.skill_id))
    return { name: 'project-skills', params: { projectId }, query: { skill: str(m?.skill_id) } }
  return null
}

export function environmentTarget(block: Block, projectId: string | null | undefined): EnvironmentTarget | null {
  const m = meta(block)
  if (!projectId || str(m?.event_type) !== 'environment_failed') return null
  const room = str(m?.room_id)
  return {
    name: 'project-settings',
    params: { projectId, section: 'environment' },
    query: room ? { room } : {},
  }
}

/**
 * 这条事件怎么渲染。
 *
 * @param block 事件块；不是 event 的（消息、附件）返回 null，由调用方按消息渲染。
 * @param run   被折叠进这一行的连续同类事件（含 block 自己）。默认只有它自己。
 */
export function platformNotice(block: Block, run: Block[] = [block]): PlatformNotice | null {
  if (block.kind !== 'event') return null

  const m = meta(block)

  // 顺序即优先级，和改动前的模板一致：事故卡 > 动作行 > 后端报错 > 折叠行 > 淡行。
  const incident = platformErrorPresentation(block)
  if (incident) {
    const { lead, rest } = splitLead(incident.body)
    const detail = noticeText(block, 'detail')
    return {
      mode: 'incident',
      incident,
      lead,
      // 被切掉的正文和 detail 都收进同一个展开区：卡面只留一句，原文一个字不少。
      rest: [rest, detail].filter(Boolean).join('\n\n'),
      detailLabel: noticeText(block, 'detail_label'),
    }
  }

  const resource = actionResource(block)
  if (resource)
    return {
      mode: 'action',
      resource,
      text: actionText(block),
      detail: noticeText(block, 'detail'),
      detailLabel: noticeText(block, 'detail_label'),
      ...docChange(block),
    }

  const mail = mailDraftView(block)
  if (mail) return { mode: 'mail-draft', mail, outcome: null }

  if (str(m?.detail)) {
    // 退回的理由是审阅的人写给芝士的话，不是日志：读时间线的人要一眼看到为什么退回，
    // 不该再点开一次。
    const quote = run.length === 1 && QUOTED_DETAIL.has(str(m?.event_type)) ? noticeText(block, 'detail') : ''
    return {
      mode: 'fold',
      line: noticeText(block),
      who: whoTag(block),
      whoLabel: whoLabel(block),
      count: run.length,
      ...(quote ? { quote } : {}),
      occurrences: quote ? [] : run.map(occurrenceOf).filter((o) => o.detail),
      retryable: m?.retryable === true,
    }
  }

  return { mode: 'plain' }
}
