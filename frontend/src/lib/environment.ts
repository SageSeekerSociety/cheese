import type { Environment, EnvironmentDevice, SandboxSpec } from '../types/compute'

import { t } from '@/i18n'

/** A sandbox spec by the name the platform gives it; one it no longer offers reads as its id. */
export function specLabel(spec: string, specs: SandboxSpec[] = []): string {
  return specs.find((s) => s.spec === spec)?.label ?? spec
}

/** 「标准沙箱」「团队设备」「实验室工作站」: a sandbox by the name the platform gives its spec, a self-hosted machine by its own. */
export function environmentName(
  env: Environment,
  devices: EnvironmentDevice[] = [],
  specs: SandboxSpec[] = []
): string {
  if (env.kind === 'self_hosted') {
    return devices.find((d) => d.device_id === env.device_id)?.name ?? t('compute.env.removedDevice')
  }
  return specLabel(env.spec, specs)
}

export function sameEnvironment(a: Environment | null | undefined, b: Environment | null | undefined): boolean {
  if (!a || !b) return !a && !b
  if (a.kind === 'sandbox' && b.kind === 'sandbox') return a.spec === b.spec
  if (a.kind === 'self_hosted' && b.kind === 'self_hosted') return a.device_id === b.device_id
  return false
}
