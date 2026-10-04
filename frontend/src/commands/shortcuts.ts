// 键盘快捷键：整个应用只有这一个监听，按键对上哪条命令就做哪条。
//
// 快捷键多半是从浏览器手里抢来的（⌘1–9 本来是切标签页），所以只在真的对上一条命令
// 时才拦下来；对不上的照旧交回给浏览器。
//
// 认 `code` 不认 `key`：`key` 跟着键盘布局走，法语 AZERTY 上不按 Shift 的那一排
// 根本不是数字，而人看着的是同一个物理键。
import type { Router } from 'vue-router'
import type { Command } from '.'

import { activeCommands } from '.'

interface Chord {
  mod: boolean
  shift: boolean
  alt: boolean
  code: string
}

function parse(shortcut: string): Chord | null {
  const parts = shortcut.toLowerCase().split('+')
  const key = parts.pop()
  if (!key) return null
  const code =
    key === 'escape'
      ? 'Escape'
      : /^[0-9]$/.test(key)
        ? `Digit${key}`
        : /^[a-z]$/.test(key)
          ? `Key${key.toUpperCase()}`
          : null
  if (!code) return null
  return { mod: parts.includes('mod'), shift: parts.includes('shift'), alt: parts.includes('alt'), code }
}

function matches(chord: Chord, event: KeyboardEvent): boolean {
  return (
    chord.code === event.code &&
    chord.mod === (event.metaKey || event.ctrlKey) &&
    chord.shift === event.shiftKey &&
    chord.alt === event.altKey
  )
}

/** 一条能给帧的键：帧只报 id，键名与修饰键留在宿主这张表里。 */
export interface FrameKey extends Chord {
  id: string
}

/** 键表上限。表要随消息走一遍，多了就成了把整个命令面发出去。 */
export const FRAME_KEY_LIMIT = 16

/** 此刻能进帧的键：按命令的登记顺序取前 N 条，禁用的不给。 */
export function frameKeys(limit = FRAME_KEY_LIMIT): FrameKey[] {
  const keys: FrameKey[] = []
  for (const command of activeCommands.value) {
    if (keys.length >= limit) break
    const chord = command.shortcut ? parse(command.shortcut) : null
    if (chord && !command.disabled) keys.push({ id: command.id, ...chord })
  }
  return keys
}

/** 做一条命令：先跑 run，再去 to。 */
export function runCommand(command: Command, router: Router) {
  if (command.disabled || command.loading) return
  command.run?.()
  if (command.to) void router.push(command.to)
}

// 装在 window 上的那一个监听握着的 router。帧里按下的键走同一个入口，
// 所以这里留一份：帧回了 id 之后跑的命令，和人在键盘上按出来的必须是同一条。
let shortcutsRouter: Router | null = null

/**
 * 帧里按下的键。只认刚发下去那张表里的 id——帧自己报不出别的命令来。
 * 返回有没有真的跑掉一条。
 */
export function runFrameKey(id: string): boolean {
  if (!shortcutsRouter) return false
  if (!frameKeys().some((key) => key.id === id)) return false
  const command = activeCommands.value.find((candidate) => candidate.id === id)
  if (!command || command.disabled || command.loading) return false
  runCommand(command, shortcutsRouter)
  return true
}

/** 挂在 window 上的那一个监听；返回撤掉它的函数。 */
export function installShortcuts(router: Router): () => void {
  shortcutsRouter = router
  const onKeydown = (event: KeyboardEvent) => {
    if (event.defaultPrevented || event.repeat) return
    const command = activeCommands.value.find((candidate) => {
      const chord = candidate.shortcut ? parse(candidate.shortcut) : null
      return chord !== null && matches(chord, event)
    })
    if (!command || command.disabled) return
    event.preventDefault()
    runCommand(command, router)
  }
  window.addEventListener('keydown', onKeydown)
  return () => {
    window.removeEventListener('keydown', onKeydown)
    if (shortcutsRouter === router) shortcutsRouter = null
  }
}
