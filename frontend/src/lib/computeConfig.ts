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

// What a choice is called. A device keeps its own name; the platform's choices
// are named here, in the reader's language, from their fields. A name stored on
// one of those is another reader's label and is not shown.
export function choiceName(c: ComputeChoice): string {
  if (c.profile === 'device') return deviceName(c.name, c.device_id)
  if (c.cores || c.memory_mb || c.disk_gb) return t('compute.choice.cloudCustom')
  return t('compute.choice.cloudStandard')
}

// A self-hosted device by its own name, or — with none known — as the platform's
// choice: any online device (no id yet, and never a stored name), or a device
// whose name was not found.
export function deviceName(name: string | null, deviceId: string | null): string {
  if (name && deviceId) return name
  return t(deviceId ? 'compute.choice.device' : 'compute.choice.anyDevice')
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
