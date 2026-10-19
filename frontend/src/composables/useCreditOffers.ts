import type { BetaCredits, CreditRequest, CreditRewards } from '@/types/creditOffers'

import { ref } from 'vue'

import {
  claimBetaCredits,
  createCreditRequest,
  getBetaCredits,
  getCreditRequests,
  getMyCreditRewards,
} from '@/api/creditOffers'

export function useCreditOffers(teamId?: () => number, personal = () => !teamId) {
  const beta = ref<BetaCredits | null>(null)
  const requests = ref<CreditRequest[]>([])
  const rewards = ref<CreditRewards | null>(null)
  const busy = ref(false)
  const error = ref<string | null>(null)
  let version = 0
  async function load() {
    const current = ++version
    beta.value = null
    rewards.value = null
    requests.value = []
    error.value = null
    try {
      const [pending, data, history] = await Promise.all([
        teamId ? getCreditRequests(teamId()) : Promise.resolve(null),
        personal() ? getBetaCredits() : Promise.resolve(null),
        personal() ? getMyCreditRewards() : Promise.resolve(null),
      ])
      if (current === version) {
        requests.value = pending?.items ?? []
        beta.value = data
        rewards.value = history
      }
    } catch (e) {
      if (current === version) error.value = e instanceof Error ? e.message : String(e)
    }
  }
  async function claim() {
    if (!personal() || busy.value) return false
    busy.value = true
    error.value = null
    try {
      await claimBetaCredits()
      await load()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return false
    } finally {
      busy.value = false
    }
  }
  async function request(kind: CreditRequest['kind'], credits?: number) {
    if (!teamId) return
    busy.value = true
    error.value = null
    try {
      await createCreditRequest(teamId(), kind, credits)
      await load()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      busy.value = false
    }
  }
  return { beta, rewards, requests, busy, error, load, claim, request }
}
