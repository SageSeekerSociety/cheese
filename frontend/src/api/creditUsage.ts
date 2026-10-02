// 芝士额度：一个人自己的，和一个团队的（成员可读）。只有比例。
import type { CreditUsage } from '@/lib/creditUsage'

import { request } from '../api'

export function getMyCreditUsage(): Promise<CreditUsage> {
  return request<CreditUsage>('/users/me/credits/usage')
}

export function getTeamCreditUsage(teamId: number): Promise<CreditUsage> {
  return request<CreditUsage>(`/teams/${teamId}/credits/usage`)
}
