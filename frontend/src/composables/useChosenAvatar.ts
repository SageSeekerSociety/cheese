import { readonly, ref } from 'vue'

import { AvatarsApi } from '@/network/api/avatars'

/**
 * "Did this person actually pick an avatar?" — and the URL if they did.
 *
 * Every registration path stamps the profile with the GLOBAL DEFAULT avatar
 * (`default_avatar_id: int = 1`, backend `domain/user/services.py`), so an
 * avatar id being present says nothing about whether anyone chose it. Rendering
 * it anyway gives every user who never picked one the same face, which is
 * strictly worse at telling people apart than the per-handle hashed initial —
 * and telling people apart is the entire job of an avatar. The backend already
 * takes this line for the rosters it serves (`ProjectRepository.list_members`);
 * this is the same rule for the payloads that carry the raw id, i.e. the
 * logged-in user's own profile.
 *
 * Which row holds the default is seed data and differs per environment, so it
 * cannot be a literal `1` here any more than it is on the server (which reads
 * `avatar_type`). Ask once, share the answer process-wide.
 */
const defaultAvatarId = ref<number | null>(null)
let inflight: Promise<void> | null = null

/**
 * Learn which avatar id is the global default. Idempotent, and safe to call on
 * every render: the first call does the request, the rest reuse it.
 *
 * A failure is swallowed on purpose and leaves the id unknown, which degrades
 * to treating every avatar as chosen — the behaviour we have today. The
 * opposite default would mistake a real avatar for the placeholder and hide it.
 */
export function ensureDefaultAvatarId(): void {
  if (defaultAvatarId.value !== null || inflight) return
  inflight = AvatarsApi.getDefaultAvatarId()
    .then(({ data }) => {
      defaultAvatarId.value = data.avatarId
    })
    .catch(() => {
      // Unknown stays unknown; see above.
    })
    .finally(() => {
      inflight = null
    })
}

/** The default avatar's id, or null while we do not know it yet. */
export const globalDefaultAvatarId = readonly(defaultAvatarId)

/** True when `avatarId` names an avatar this person deliberately picked. */
export function isChosenAvatar(avatarId?: number | null): boolean {
  if (avatarId == null) return false
  return avatarId !== defaultAvatarId.value
}
