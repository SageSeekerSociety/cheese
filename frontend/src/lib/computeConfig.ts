import type { ComputeChoice } from '../cx_types'

export function choiceKey(c: ComputeChoice): string {
  return JSON.stringify([c.profile, c.device_id ?? null, c.cores ?? null, c.memory_mb ?? null, c.disk_gb ?? null])
}

// The choices in order, each configuration once.
export function compactChoices(...choices: (ComputeChoice | null | undefined)[]): ComputeChoice[] {
  const seen = new Set<string>()
  return choices.filter((c): c is ComputeChoice => {
    if (!c) return false
    const key = choiceKey(c)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function choiceDetail(c: ComputeChoice): string {
  if (c.profile === 'device') return c.device_id ? '自有设备' : '首次运行时选择在线设备'
  if (!c.cores && !c.memory_mb && !c.disk_gb) return '平台标准配置 · 首次运行时自动准备'
  return [
    c.cores && `${c.cores} 核 CPU`,
    c.memory_mb && `${c.memory_mb / 1024} GB 内存`,
    c.disk_gb && `${c.disk_gb} GB 磁盘`,
  ]
    .filter(Boolean)
    .join(' · ')
}
