import type { ComputeChoice } from '../cx_types'

import { t } from '@/i18n'

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
  if (c.profile === 'device') return c.device_id ? t('compute.choice.device') : t('compute.choice.deviceOnFirstRun')
  if (!c.cores && !c.memory_mb && !c.disk_gb) return t('compute.choice.standard')
  return [
    c.cores && t('compute.choice.cores', { n: c.cores }),
    c.memory_mb && t('compute.choice.memory', { n: c.memory_mb / 1024 }),
    c.disk_gb && t('compute.choice.disk', { n: c.disk_gb }),
  ]
    .filter(Boolean)
    .join(' · ')
}
