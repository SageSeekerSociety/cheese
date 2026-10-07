// 一步的名字要和现场那一行说的是同一个词，参数是这次调用真正在动的那样东西 ——
// 所以动词拿 toolLabel 的结果比对，参数直接比对调用里的原文。
import type { LiveBlock } from '../types/live'

import { beforeEach, describe, expect, it } from 'vitest'

import { liveStep, stepText } from './liveStep'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

function tool(name: string, args: unknown): LiveBlock {
  return { type: 'tool', id: 'c1', name, arguments: JSON.stringify(args) }
}

describe('the step an agent is on, read from its live frame', () => {
  it('reads a shell command as verb + command', () => {
    const step = liveStep([tool('Bash', { command: 'pnpm test', description: 'run the suite' })])
    expect(step).toEqual({ verb: '执行命令', arg: 'pnpm test' })
    expect(stepText(step!)).toBe('执行命令 pnpm test')
  })

  it('reads a file path for the tools whose argument is a path', () => {
    expect(liveStep([tool('Read', { file_path: 'src/main.ts' })])).toEqual({ verb: '读取文件', arg: 'src/main.ts' })
  })

  it('shows the argument that is still streaming, up to what is written', () => {
    const block: LiveBlock = { type: 'tool', id: 'c1', name: 'Bash', arguments: '{"command":"pnpm ru' }
    expect(liveStep([block])!.arg).toBe('pnpm ru')
  })

  it('strips the MCP server prefix so the verb is not left raw', () => {
    expect(liveStep([tool('mcp__native__cheese_task', { title: '修回退' })])).toEqual({
      verb: '创建任务',
      arg: '修回退',
    })
  })

  it('falls back to the verb alone when the tool carries no telling argument', () => {
    expect(liveStep([tool('cheese_doc_get', { path: 'x' })])).toEqual({ verb: '读取文档', arg: '' })
  })

  it('says nothing when no tool call is being written', () => {
    expect(liveStep([])).toBeNull()
    expect(liveStep([{ type: 'text', text: 'thinking out loud' }])).toBeNull()
  })

  it('flattens a multi-line argument onto one line', () => {
    expect(liveStep([tool('Bash', { command: 'echo a\necho b' })])!.arg).toBe('echo a echo b')
  })
})
