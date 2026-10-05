import type { ComputeChoice } from '../types/compute'

import { t } from '@/i18n'

export function choiceKey(c: ComputeChoice): string {
  return JSON.stringify([c.profile, c.device_id ?? null, c.whole_machine === true])
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
  return t(c.whole_machine ? 'compute.choice.cloudVm' : 'compute.choice.cloud')
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
  return t(c.whole_machine ? 'compute.choice.vm' : 'compute.choice.sandbox')
}
