// 芝士一轮里做的事，在面板上画成什么：每一步（动词 + 一个参数），改动卡，结果。这里是
// 不管写的是什么都一样的那一半——面板拿的形状，和每一步怎么说；写进题目草稿的改动卡、
// 发布的结果怎么说在 `lib/draftChangeView.ts`。
import type { AssistantProposal, AssistantStep } from '@/stores/agentThread'

import { t } from '@/i18n'
import { partialStringField } from '@/lib/partialJson'
import { toolKey, toolLabel } from '@/lib/toolLabels'

export interface PanelStep {
  key: string
  verb: string
  arg: string
  state: AssistantStep['state']
  error?: string
  /** 推理的那一段，不是一次工具调用。 */
  thinking?: boolean
}

export interface PanelChangeRow {
  /** 改这一格的那次写入（撤销时用）。 */
  change: number
  /** 芝士的字段名（`AGENT_FIELDS` 的键）。 */
  key: string
  label: string
  value: string
  undone: boolean
  /** 人在芝士读过之后改过这一格，芝士没动它。 */
  kept?: boolean
}

export interface PanelChange {
  id: number
  /** 不是写草稿的改动（通知设置、频道提醒）：卡头写它，不写「表单改动」。 */
  title?: string
  draft: number
  /** 新建的草稿：卡上写它的名字，点开切过去。 */
  created: boolean
  name: string
  rows: PanelChangeRow[]
  notes: { key: string; label: string; text: string }[]
  canUndo: boolean
  /** 一份文件拆出几道题时，这是第几道（卡头写「第 N 道」）。 */
  ordinal?: number
  /** 这一轮也写了、但不是眼前这份的草稿：只给一行，点名字切过去。 */
  collapsed?: boolean
}

/** 一轮回答写进草稿的样子：眼前这份草稿改了哪几格（一张卡）。 */
export interface PanelWrites {
  changes: PanelChange[]
}

/** 芝士起草、等人确认的一件事，卡上画的样子。`where` 是发到哪（频道、任务或支线的名字），
 *  `title` 只有任务有；`text` 是消息正文或任务的交代，确认前人能改。 */
export interface PanelProposal {
  id: number
  kind: AssistantProposal['kind']
  state: AssistantProposal['state']
  where: string
  title?: string
  text: string
  /** 开出的任务：卡上给「查看」。 */
  task?: string
  /** `act`：为什么先问人。 */
  reason?: string
}

export function proposalView(p: AssistantProposal): PanelProposal {
  if (p.kind === 'act')
    return {
      id: p.id,
      kind: p.kind,
      state: p.state,
      where: '',
      text: p.summary ?? '',
      reason: p.reason ?? '',
    }
  return {
    id: p.id,
    kind: p.kind,
    state: p.state,
    where: (p.kind === 'task' ? p.channelName : p.topicName) ?? '',
    ...(p.kind === 'task' ? { title: p.title ?? '' } : {}),
    text: (p.kind === 'task' ? p.brief : p.text) ?? '',
    ...(p.result?.task ? { task: p.result.task } : {}),
  }
}

export interface PanelResult {
  taskId: number
  title: string
  sub: string
}

/** 每种工具这一行跟的那个参数。 */
const STEP_ARG: Record<string, string> = {
  cheese_task_search: 'query',
  cheese_problem_write: 'name',
  cheese_file_read: 'pages',
  cheese_docs_search: 'query',
  cheese_docs_read: 'page',
  cheese_chat_search: 'query',
  cheese_project_search: 'query',
  cheese_task_draft: 'title',
  cheese_read: 'ref',
  cheese_find: 'query',
  cheese_help: 'query',
  cheese_message_send: 'where',
  cheese_task_open: 'title',
  cheese_review_decide: 'ref',
  cheese_inbox_respond: 'ref',
  cheese_member_set: 'who',
  cheese_channel_create: 'title',
  cheese_web_fetch: 'url',
  cheese_web_search: 'query',
  cheese_settings_set: 'key',
  cheese_routine: 'title',
  cheese_notify_me: 'text',
  cheese_memory: 'description',
  cheese_file_give: 'name',
  cheese_file_to_computer: 'ref',
  // 这段对话自己那台电脑上的（pi 自己的工具）。
  bash: 'command',
  read: 'path',
  write: 'path',
  edit: 'path',
  ls: 'path',
  find: 'pattern',
  grep: 'pattern',
}

export function stepView(step: AssistantStep): PanelStep {
  if (step.name === 'thinking')
    return {
      key: step.call,
      verb:
        step.state === 'run'
          ? t('tasks.assistant.steps.thinking')
          : t('tasks.assistant.steps.thought', { s: Math.max(1, Math.round((step.ms ?? 0) / 1000)) }),
      arg: '',
      state: step.state,
      thinking: true,
    }
  const key = STEP_ARG[toolKey(step.name, step.args)] ?? STEP_ARG[step.name]
  const arg = key ? partialStringField(step.args, key)?.text ?? '' : ''
  return {
    key: step.call,
    verb: toolLabel(step.name, step.args),
    arg: arg.replace(/\s+/g, ' ').trim().slice(0, 60),
    state: step.state,
    error: step.error === undefined ? undefined : stepError(step.error),
  }
}

/** 一步为什么没成，写成人读得懂的一句。工具报的是平台原样的回应（`Platform HTTP 404:
 * {...}`）时只说是哪一类；芝士自己读得到原文，接着会说它怎么办。平台拒绝时说的那句中文
 * 原样留着。 */
export function stepError(text: string): string {
  const status = /^Platform HTTP (\d{3})/.exec(text)?.[1]
  if (status === '404') return t('tasks.assistant.steps.notFound')
  if (status === '401' || status === '403') return t('tasks.assistant.steps.denied')
  if (status || !/\p{Script=Han}/u.test(text) || text.includes('{')) return t('tasks.assistant.steps.failed')
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}
