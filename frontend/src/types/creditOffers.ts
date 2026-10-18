export interface BetaCredits {
  credits: number
  claimed: boolean
  feedback_credits: number
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
