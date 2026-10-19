import type { BetaCredits, CreditRequest, CreditRewards, PublishedCreditPlan } from '@/types/creditOffers'

import { request } from '../api'

export const getBetaCredits = () => request<BetaCredits>('/users/me/credits/beta')
export const getMyCreditRewards = () => request<CreditRewards>('/users/me/credits/rewards')
export const getPublishedCreditPlans = () => request<{ plans: PublishedCreditPlan[] }>('/credits/plans')
export const claimBetaCredits = () =>
  request<{ credits: number; issued: boolean }>('/users/me/credits/beta', { method: 'POST' })
export const getCreditRequests = (teamId: number) =>
  request<{ items: CreditRequest[] }>(`/teams/${teamId}/credits/requests`)
export const createCreditRequest = (teamId: number, kind: CreditRequest['kind'], credits?: number) =>
  request<CreditRequest>(`/teams/${teamId}/credits/requests`, {
    method: 'POST',
    body: JSON.stringify({ kind, credits }),
  })
export const getPendingCreditRequests = () => request<{ items: CreditRequest[] }>('/admin/credits/requests')
export const handleCreditRequest = (id: string, approve: boolean, paymentConfirmed: boolean, planKey?: string) =>
  request<CreditRequest>(`/admin/credits/requests/${id}`, {
    method: 'POST',
    body: JSON.stringify({ approve, payment_confirmed: paymentConfirmed, plan_key: planKey }),
  })
export const rewardFeedback = (id: string) =>
  request<{ credits: number; issued: boolean }>(`/admin/feedback/${id}/reward`, { method: 'POST' })
