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
  const code = /^[0-9]$/.test(key) ? `Digit${key}` : /^[a-z]$/.test(key) ? `Key${key.toUpperCase()}` : null
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

/** 做一条命令：先跑 run，再去 to。 */
export function runCommand(command: Command, router: Router) {
  if (command.disabled || command.loading) return
  command.run?.()
  if (command.to) void router.push(command.to)
}

/** 挂在 window 上的那一个监听；返回撤掉它的函数。 */
export function installShortcuts(router: Router): () => void {
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
  return () => window.removeEventListener('keydown', onKeydown)
}
