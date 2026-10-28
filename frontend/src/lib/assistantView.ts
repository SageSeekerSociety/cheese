// 芝士一轮里做的事，在面板上画成什么：每一步（动词 + 一个参数），改动卡，结果。这里是
// 不管写的是什么都一样的那一半——面板拿的形状，和每一步怎么说；写进题目草稿的改动卡、
// 发布的结果怎么说在 `lib/draftChangeView.ts`。
import type { AssistantProposal, AssistantStep, AssistantSubagent, StepEffect } from '@/stores/agentThread'

import { t } from '@/i18n'
import { partialStringField } from '@/lib/partialJson'
import { toolKey, toolLabel } from '@/lib/toolLabels'

export interface PanelStep {
  key: string
  verb: string
  arg: string
  state: AssistantStep['state']
  error?: string
  /** 哪一个动作（`chat_send`、`cheese_web.fetch`）：收起的那句按它数发了几条消息。 */
  tool?: string
  /** 它做的是哪一类事（工具表上写的）：查阅的几步并成一组，做了事的单列。 */
  effect?: StepEffect
  /** 推理的那一段，不是一次工具调用。 */
  thinking?: boolean
  /** 推理用了几秒：太短的不列。 */
  seconds?: number
  /** 它前面回答已经写了多少字：步骤排在这些字后面。旧的回答没有。 */
  at?: number
  /** 不在步骤里画出来的一步：写进度清单，清单另画在这一轮的最上面。 */
  hidden?: boolean
  /** 这一步写下的进度清单。 */
  checklist?: ChecklistItem[]
  /** 这一步派出的分身：做到哪了，做过的几步。 */
  subagent?: { status: AssistantSubagent['status']; steps: PanelStep[] }
}

/** 把这一轮派出的分身挂到派它的那一步上：那一步跟着分身转圈、打勾，点开看它做过的几步。 */
export function withSubagents(steps: PanelStep[], subagents: AssistantSubagent[] = []): PanelStep[] {
  if (!subagents.length) return steps
  const by = new Map(subagents.map((s) => [s.call, s]))
  return steps.map((step) => {
    const found = by.get(step.key)
    if (!found) return step
    return {
      ...step,
      state: found.status === 'running' ? 'run' : found.status === 'failed' ? 'failed' : 'done',
      subagent: {
        status: found.status,
        steps: found.steps.map((done, i) =>
          stepView({ call: `${found.id}-${i}`, name: done.name, args: done.args, state: 'done', shown: done.shown })
        ),
      },
    }
  })
}

/** 进度清单的一项：这一步做什么，做到哪了。 */
export interface ChecklistItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}

const CHECKLIST_STATES = new Set(['pending', 'in_progress', 'completed'])

/** 一次写清单的调用写下的清单；参数还没写完、读不出来时没有。 */
function checklistIn(args: string): ChecklistItem[] | undefined {
  try {
    const todos = (JSON.parse(args) as { todos?: unknown }).todos
    if (!Array.isArray(todos)) return undefined
    return todos
      .filter(
        (item): item is ChecklistItem =>
          typeof item?.content === 'string' && CHECKLIST_STATES.has(item?.status as string)
      )
      .map((item) => ({ content: item.content, status: item.status }))
  } catch {
    return undefined
  }
}

/** 这一轮最后写下的那份进度清单；没写过就没有。 */
export function checklistOf(steps: PanelStep[]): ChecklistItem[] | null {
  for (let i = steps.length - 1; i >= 0; i--) if (steps[i].checklist) return steps[i].checklist ?? null
  return null
}

/** 一轮回答按发生的顺序排开：说的一段话，或接连做的几步。 */
export type TurnPiece = { kind: 'text'; key: string; text: string } | { kind: 'steps'; key: string; steps: PanelStep[] }

/** 把一轮回答的话和步骤按发生的顺序排开：每一步排在它之前写下的那些字后面，接连的几步
 *  归成一组。没记位置的步骤（更早的回答）都排在话前面，和从前一样。 */
export function interleave(text: string, steps: PanelStep[]): TurnPiece[] {
  const pieces: TurnPiece[] = []
  let from = 0
  const say = (to: number) => {
    const said = text.slice(from, to).trim()
    if (said) pieces.push({ kind: 'text', key: `text-${from}`, text: said })
    from = to
  }
  for (const step of steps) {
    if (step.hidden) continue
    const at = Math.min(Math.max(step.at ?? 0, from), text.length)
    if (at > from) say(at)
    const last = pieces[pieces.length - 1]
    if (last?.kind === 'steps') last.steps.push(step)
    else pieces.push({ kind: 'steps', key: `steps-${step.key}`, steps: [step] })
  }
  say(text.length)
  return pieces
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
  'cheese_problem.write': 'name',
  cheese_file_read: 'pages',
  'cheese_chat.search': 'query',
  cheese_project_search: 'query',
  cheese_task_draft: 'title',
  cheese_read: 'ref',
  cheese_find: 'query',
  cheese_help: 'query',
  chat_send: 'where',
  'cheese_task.open': 'title',
  'cheese_card.decide': 'ref',
  'cheese_inbox.respond': 'ref',
  cheese_people: 'who',
  'cheese_room.member': 'who',
  'cheese_room.create': 'title',
  'cheese_web.fetch': 'url',
  'cheese_web.search': 'query',
  cheese_routine: 'title',
  cheese_notify: 'text',
  cheese_memory: 'description',
  cheese_subagent_start: 'description',
  cheese_subagent_send: 'message',
  'cheese_library.give': 'name',
  'cheese_library.take': 'ref',
  // 这段对话自己那台电脑上的（pi 自己的工具）。
  bash: 'command',
  read: 'path',
  write: 'path',
  edit: 'path',
  ls: 'path',
  find: 'pattern',
  grep: 'pattern',
}

/** 这几个参数是芝士指代东西用的编号（`ml-course/channels/3`、`alerts/12`），人认不出，
 * 不写在这一行上；资料库里的文件编号就是它的路径，写出文件名。 */
const REF_ARGS = new Set(['ref', 'where'])

function shownArg(key: string, value: string): string {
  if (!REF_ARGS.has(key)) return value
  const file = /^library\/(.+)$/.exec(value.trim())?.[1]
  return file ? file.split('/').pop() ?? '' : ''
}

export function stepView(step: AssistantStep): PanelStep {
  if (step.name === 'thinking') {
    const seconds = Math.max(1, Math.round((step.ms ?? 0) / 1000))
    return {
      key: step.call,
      verb:
        step.state === 'run' ? t('tasks.assistant.steps.thinking') : t('tasks.assistant.steps.thought', { s: seconds }),
      arg: '',
      state: step.state,
      thinking: true,
      seconds,
      ...(step.at === undefined ? {} : { at: step.at }),
    }
  }
  if (step.name === 'cheese_todo')
    return {
      key: step.call,
      verb: toolLabel(step.name),
      arg: '',
      state: step.state,
      hidden: true,
      checklist: checklistIn(step.args),
    }
  const tool = toolKey(step.name, step.args)
  const key = STEP_ARG[tool] ?? STEP_ARG[step.name]
  // 读到的东西有名字时写名字，不写编号。
  const arg = step.shown ?? (key ? shownArg(key, partialStringField(step.args, key)?.text ?? '') : '')
  return {
    key: step.call,
    verb: step.effect === 'reads' ? readVerb(tool, step.args) : toolLabel(step.name, step.args),
    arg: arg.replace(/\s+/g, ' ').trim().slice(0, 60),
    state: step.state,
    tool,
    ...(step.effect ? { effect: step.effect } : {}),
    error: step.error === undefined ? undefined : stepError(step.error),
    ...(step.at === undefined ? {} : { at: step.at }),
  }
}

/** 查阅的一步用哪个动词：按关键词或样式找的是「查找」，搜索引擎和帮助文档按词搜的是
 *  「搜索」，其余（读一个东西、一页帮助、一个网页、电脑上的一个文件）是「读取」。 */
function readVerb(tool: string, args: string): string {
  if (tool === 'cheese_find' || tool === 'find' || tool === 'grep') return t('tasks.assistant.steps.verbs.find')
  const searches =
    tool === 'cheese_web.search' ||
    tool === 'cheese_project_search' ||
    (tool === 'cheese_help' && !partialStringField(args, 'page')?.text)
  return t(searches ? 'tasks.assistant.steps.verbs.search' : 'tasks.assistant.steps.verbs.read')
}

/** 推理不到这么多秒的一段不列：它只是两步之间停了一下。 */
const THOUGHT_LISTED_S = 10

/** 一组步骤画成的几行：接连查阅的几步并成一组，各写一行查了什么；做了事的、失败的、
 *  派出分身的每步单列。 */
export type StepRow =
  | { kind: 'step'; key: string; step: PanelStep }
  | { kind: 'reads'; key: string; steps: PanelStep[] }

export function stepRows(steps: PanelStep[]): StepRow[] {
  const rows: StepRow[] = []
  for (const step of steps) {
    if (step.thinking && step.state !== 'run' && (step.seconds ?? 0) < THOUGHT_LISTED_S) continue
    if (step.effect === 'reads' && step.state !== 'failed' && !step.subagent) {
      const last = rows[rows.length - 1]
      if (last?.kind === 'reads') last.steps.push(step)
      else rows.push({ kind: 'reads', key: `reads-${step.key}`, steps: [step] })
      continue
    }
    rows.push({ kind: 'step', key: step.key, step })
  }
  return rows
}

/** 一组查阅的那一行：正在读的那一项，读完了就是一共几项。 */
export function readsLine(steps: PanelStep[]): string {
  const running = steps.find((step) => step.state === 'run')
  if (running) return t('tasks.assistant.steps.reading', { what: `${running.verb}${running.arg}` })
  return t('tasks.assistant.steps.readCount', { n: steps.length })
}

/** 收起的那一句：先说做了什么，再说查阅了几项。发消息按条数；别的事只有一件时写这件事，
 *  几件时写一共几项。从前的回答没记每一步做的是哪类事，只说一共几步。 */
export function foldedLine(steps: PanelStep[]): string {
  const calls = steps.filter((step) => !step.thinking)
  if (!calls.some((step) => step.effect)) return t('tasks.assistant.steps.count', { n: calls.length })
  const done = calls.filter((step) => step.effect !== 'reads')
  const sent = done.filter((step) => step.tool === 'chat_send').length
  const others = done.filter((step) => step.tool !== 'chat_send')
  const reads = calls.filter((step) => step.effect === 'reads').length
  const parts: string[] = []
  if (sent) parts.push(t('tasks.assistant.steps.sent', { n: sent }))
  if (others.length === 1) parts.push(`${others[0].verb} ${others[0].arg}`.trim())
  else if (others.length > 1) parts.push(t('tasks.assistant.steps.acted', { n: others.length }))
  if (reads) parts.push(t('tasks.assistant.steps.readCount', { n: reads }))
  return parts.join(t('tasks.assistant.steps.and'))
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
