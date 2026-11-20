/**
 * 领不了一道题的原因怎么说。后端给的 `message` 是英文日志句，不给人看；按 `code`
 * 说中文，认不出的给一句通用的。题目页的提示条和选团队的对话框用的是同一份。
 */
const REASON_KEYS: Record<string, string> = {
  TASK_NOT_APPROVED: 'tasks.eligibility.TASK_NOT_APPROVED',
  REGISTRATION_NOT_STARTED: 'tasks.eligibility.REGISTRATION_NOT_STARTED',
  PARTICIPANT_LIMIT_REACHED: 'tasks.eligibility.PARTICIPANT_LIMIT_REACHED',
  ALREADY_PARTICIPATING: 'tasks.eligibility.ALREADY_PARTICIPATING',
  MEMBER_ALREADY_PARTICIPATING: 'tasks.eligibility.MEMBER_ALREADY_PARTICIPATING',
  MISSING_REAL_NAME: 'tasks.eligibility.MISSING_REAL_NAME',
  USER_RANK_NOT_HIGH_ENOUGH: 'tasks.eligibility.USER_RANK_NOT_HIGH_ENOUGH',
  TEAM_TOO_SMALL: 'tasks.eligibility.TEAM_TOO_SMALL',
  TEAM_TOO_LARGE: 'tasks.eligibility.TEAM_TOO_LARGE',
  TEAM_MEMBER_MISSING_REAL_NAME: 'tasks.eligibility.TEAM_MEMBER_MISSING_REAL_NAME',
  TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH: 'tasks.eligibility.TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH',
}

/** 直接开始的题没有报名、没有申请：这几条按「开始做」「开放」说。 */
const DIRECT_REASON_KEYS: Record<string, string> = {
  TASK_NOT_APPROVED: 'tasks.eligibility.direct.TASK_NOT_APPROVED',
  REGISTRATION_NOT_STARTED: 'tasks.eligibility.direct.REGISTRATION_NOT_STARTED',
  ALREADY_PARTICIPATING: 'tasks.eligibility.direct.ALREADY_PARTICIPATING',
  MEMBER_ALREADY_PARTICIPATING: 'tasks.eligibility.direct.MEMBER_ALREADY_PARTICIPATING',
}

/** 这个原因对应的词条，按这道题怎么开始说。 */
export function eligibilityReasonKey(code: string | undefined, startsDirectly = false): string {
  const key = code ?? ''
  return (startsDirectly && DIRECT_REASON_KEYS[key]) || REASON_KEYS[key] || 'tasks.eligibility.unknown'
}
