import type { Team, TeamPermission } from '@/types'

/**
 * Whether the reader may do `permission` to the team, as the server answered it
 * (`viewerPermissions` on a team read by id or handle). The server's answer, not
 * the reader's `role`: a platform admin manages every shared team without a seat.
 */
export function teamAllows(team: Team | undefined | null, permission: TeamPermission): boolean {
  return !!team?.viewerPermissions?.includes(permission)
}
