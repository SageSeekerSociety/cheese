import type { BetaCredits, CreditRequest } from '@/types/creditOffers'

import { ref } from 'vue'

import { claimBetaCredits, createCreditRequest, getBetaCredits, getCreditRequests } from '@/api/creditOffers'

export function useCreditOffers(teamId?: () => number) {
  const beta = ref<BetaCredits | null>(null)
  const requests = ref<CreditRequest[]>([])
  const busy = ref(false)
  const error = ref<string | null>(null)
  let version = 0
  async function load() {
    const current = ++version
    beta.value = null
    requests.value = []
    error.value = null
    try {
      if (teamId) {
        const data = await getCreditRequests(teamId())
        if (current === version) requests.value = data.items
      } else {
        const data = await getBetaCredits()
        if (current === version) beta.value = data
      }
    } catch (e) {
      if (current === version) error.value = e instanceof Error ? e.message : String(e)
    }
  }
  async function claim() {
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
  return { beta, requests, busy, error, load, claim, request }
}
