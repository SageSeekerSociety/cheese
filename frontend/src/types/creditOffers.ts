export interface BetaCredits {
  credits: number
  claimed: boolean
  feedback_credits: number
}

export interface CreditRewards {
  feedback_count: number
  feedback_credits: number
  items: { kind: 'beta' | 'feedback'; credits: number; created_at: string }[]
}

export interface PublishedCreditPlan {
  key: string
  name: string
  audience: 'personal' | 'team' | 'both'
  credits_per_period: number | null
  period: string
  windows: { hours?: number | null; calendar?: 'week' | 'month' | null; credits: number }[]
  unlimited: boolean
  models: string[]
}

export interface CreditRequest {
  id: string
  team_id: number
  team_name: string
  kind: 'team_plan' | 'top_up'
  credits: number | null
  status: 'pending' | 'completed' | 'declined'
  requester_handle: string
  created_at: string
}
