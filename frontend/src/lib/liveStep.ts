// 一位队友此刻在做的那一步，从它正在写的那一帧里读出来（types/live.ts）。
//
// 现场 (PanelSite) 把一次工具调用画成「动词 + 参数」两截；那一行要等这一步的
// 记录落库之后才有。待在输入框下面的那一行等不了：一次 Bash 能跑几分钟，这期间
// 时间线上一个字都不长，读的人只看见「正在工作… · 思考中」。所以这里拿同一次
// 调用还没落下时的原始帧，先说一句「在做什么」。动词走 `toolLabel`，和现场那一
// 行查的是同一张动词表；但落库那一行由后端重写过（命令说明、读文件归类等），
// 所以两处的字面可以不同，比如这里先是「执行命令 cat x.py」，落库后是「读取
// 文件 x.py」。各自都不假，别承诺两句一字不差。
//
// 参数从还在流的 JSON 里取，取哪个键由 backend 的 `_TOOL_ARG`
// (domain/agent/tool_preview.py) 定：这一步在动什么，就取那个参数。表里没有的
// 工具只显示动词 —— 硬找一个参数填进去，比留空更让人读不懂。

import type { LiveBlock } from '../types/live'

import { partialStringField } from './partialJson'
import { toolLabel } from './toolLabels'

type ToolBlock = Extract<LiveBlock, { type: 'tool' }>

/** 一次工具调用里最能说明问题的那个参数。名字是「工具」，值是参数的键。 */
const ARG_KEY: Record<string, string> = {
  // cheese 平台动作
  update_doc: 'content',
  remember: 'fact',
  notify: 'title',
  request_accept: 'reviewer_handle',
  pin_milestone: 'title',
  write_file: 'path',
  record_decision: 'decision',
  // Claude Code 原生工具
  Bash: 'command',
  Write: 'file_path',
  Edit: 'file_path',
  Read: 'file_path',
  Glob: 'pattern',
  Grep: 'pattern',
  WebSearch: 'query',
  WebFetch: 'url',
  Agent: 'description',
  Task: 'description',
  NotebookEdit: 'notebook_path',
  Skill: 'skill',
  ToolSearch: 'query',
  // pi 原生工具。名字全小写，参数名自成一套，和上面那批没有一个重合。
  bash: 'command',
  read: 'path',
  write: 'path',
  edit: 'path',
  ls: 'path',
  find: 'pattern',
  grep: 'pattern',
  // 平台工具表里的工具，和 pi 房间里由 CLI 命令变成的工具。
  chat_send: 'content',
  chat_edit: 'content',
  cheese_chat_search: 'query',
  cheese_doc_set: 'path',
  cheese_doc_edit: 'reason',
  cheese_task: 'title',
  cheese_close_task: 'conclusion',
  cheese_fetch: 'url',
  cheese_lock: 'kind',
  cheese_unlock: 'kind',
  cheese_decision: 'text',
  cheese_title: 'text',
  cheese_notify: 'title',
  cheese_ask: 'question',
  cheese_accept_request: 'subject',
  cheese_describe: 'subject',
  cheese_tell: 'message',
  cheese_milestone: 'title',
  cheese_serve: 'note',
  cheese_show: 'path',
  // 后台任务。
  bash_start: 'command',
  bash_read: 'id',
  bash_write: 'text',
  bash_kill: 'id',
}

/** `mcp__<服务器>__<工具>` → `<工具>`。和 `toolLabel` 认的是同一个前缀。 */
const MCP_PREFIX = /^mcp__[a-z0-9_]+__/

/** 参数只占这一行的一截，超过就截断 —— 这一行是扫一眼的，不是读的。 */
export const STEP_ARG_MAX = 80

export interface LiveStep {
  /** 这一步的动词，和现场那一行同一个词。 */
  verb: string
  /** 动词后面跟的那截参数原文（已折成一行、封顶）；没有就是空串。 */
  arg: string
}

/** 一帧里此刻在写的那次工具调用。没有就不说。 */
function currentTool(blocks: LiveBlock[]): ToolBlock | null {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i]
    if (block.type === 'tool' && block.name) return block
  }
  return null
}

/** 空白折成一个空格：帧里的 JSON 可能带换行，这一行只放得下一句。 */
function collapse(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/**
 * 这一帧说出来的一步，或 null（此刻没在写工具）。
 *
 * 帧是还在写的原文，所以参数用 `partialStringField` 读 —— 没写完的 JSON，解析器
 * 只取到能确定的那一截，和正在写的那条消息同一个读法。
 */
export function liveStep(blocks: LiveBlock[]): LiveStep | null {
  const block = currentTool(blocks)
  if (!block) return null
  const name = (block.name as string).replace(MCP_PREFIX, '')
  const verb = toolLabel(name)
  const key = ARG_KEY[name]
  const raw = key ? partialStringField(block.arguments, key)?.text ?? '' : ''
  return { verb, arg: collapse(raw).slice(0, STEP_ARG_MAX) }
}

/** 这一步写成一句话：有参数就「动词 参数」，没有就只有动词。 */
export function stepText(step: LiveStep): string {
  return step.arg ? `${step.verb} ${step.arg}` : step.verb
}
