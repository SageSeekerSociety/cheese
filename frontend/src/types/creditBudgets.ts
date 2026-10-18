export interface MemberCreditBudget {
  name?: string
  credits_limit: number | null
  credits_used: number
}

export interface ProjectCreditBudget {
  credits_limit: number | null
  credits_used: number
  credits_remaining: number | null
  public_credits: number
  unattributed_credits: number
  members: Record<string, MemberCreditBudget>
  since: string
}

export interface CreditBudgetUpdate {
  credits_limit?: number | null
  member_limits?: Record<string, number | null>
}
