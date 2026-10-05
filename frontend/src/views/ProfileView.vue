<script setup lang="ts">
// 个人主页：一个人是谁、这一年做了多少、在哪些项目里、最近参与了哪些话题。
//
// 一个页面，两个入口：
//   - 站内任何地方点一个人 → `/users/:handle`，全站的页面；
//   - 项目的成员名册里点一个人 → `/projects/:projectId/members/:handle`，留在项目
//     这个框里（页头「成员 / 名字」），右栏最前面多一段「在这个项目里」。
// 除此之外两个入口画的是同一页。
//
// 取数（主页/摘要/话题）、头像 id 是不是「选过的」、选一周时那一次拉取、「删掉一条
// 理解」的请求、页头和标签页标题，都在这一半；画面在 ProfileViewView.vue，只收
// props 只发事件。
import type { MemberSummary, ProfileTopic, ProfileUnderstanding, UserProfile } from '@/cx_types'
import type { ActivityWeek } from '@/lib/activityYear'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import { useCachedResource } from '@/composables/useCachedResource'
import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { usePageTitle } from '@/composables/usePageTitle'

import ProfileViewView from './ProfileViewView.vue'

import { deleteUnderstanding, getMemberSummary, getUserProfile, getUserTopics } from '@/api'
import { t } from '@/i18n'
import { userRefRoute, type UserRefTarget } from '@/lib/userRef'

defineOptions({ name: 'ProfileView' })

const props = defineProps<{ handle: string; projectId?: string }>()

/** 「最近参与的话题」列几个；选中一周时列那一周的全部（接口上限 50）。 */
const RECENT_TOPICS = 8
const WEEK_TOPICS = 50

const route = useRoute()
const router = useRouter()

ensureDefaultAvatarId()

interface Page {
  profile: UserProfile
  member: MemberSummary | null
  /** 最近参与的话题；这一段单独失败时是 null，页面其余部分照常。 */
  recent: ProfileTopic[] | null
}

const { data, loading, error, refresh } = useCachedResource(
  () => (props.projectId ? `profile:${props.projectId}:${props.handle}` : `profile:${props.handle}`),
  async (): Promise<Page> => {
    const [profile, member, recent] = await Promise.all([
      getUserProfile(props.handle),
      props.projectId ? getMemberSummary(props.projectId, props.handle).catch(() => null) : Promise.resolve(null),
      getUserTopics(props.handle, { limit: RECENT_TOPICS })
        .then((r) => r.topics)
        .catch(() => null),
    ])
    return { profile, member, recent }
  }
)

const profile = computed(() => data.value?.profile ?? null)
const member = computed(() => data.value?.member ?? null)
const displayName = computed(() => profile.value?.name || props.handle)

const avatarUrl = computed(() => {
  const id = profile.value?.avatar_id
  return isChosenAvatar(id) ? getAvatarUrl(id!) : ''
})

// 顶栏（手机）和标签页标题写这个人的名字，而不是路由上那个泛泛的「成员」。
const { setDynamicTitle } = usePageTitle()
watch(
  () => (profile.value ? displayName.value : ''),
  (name) => {
    if (name) setDynamicTitle(name, props.projectId ? 'member' : 'UserPage')
  },
  { immediate: true }
)

// ---- 话题：最近，或者选中的那一周 ----
const selectedWeek = ref<ActivityWeek | null>(null)
const weekTopics = ref<ProfileTopic[] | null>(null)
const weekPending = ref(false)
const weekFailed = ref(false)
let weekRequest = 0

function clearWeek() {
  weekRequest += 1
  selectedWeek.value = null
  weekTopics.value = null
  weekPending.value = false
  weekFailed.value = false
}

async function pickWeek(week: ActivityWeek) {
  if (selectedWeek.value?.from === week.from) {
    clearWeek()
    return
  }
  const seq = ++weekRequest
  selectedWeek.value = week
  weekPending.value = true
  weekFailed.value = false
  try {
    const { topics } = await getUserTopics(props.handle, { from: week.from, to: week.to, limit: WEEK_TOPICS })
    if (seq !== weekRequest) return
    weekTopics.value = topics
  } catch {
    if (seq !== weekRequest) return
    weekTopics.value = null
    weekFailed.value = true
  } finally {
    if (seq === weekRequest) weekPending.value = false
  }
}

// 换了一个人，上一个人选中的那一周不跟过来。
watch(() => props.handle, clearWeek)

// 先从列表里拿掉，再去删；服务器不肯就放回原处。
async function forget(note: ProfileUnderstanding) {
  const notes = data.value?.profile.understanding
  if (!notes) return
  const at = notes.findIndex((n) => n.id === note.id)
  if (at < 0) return
  notes.splice(at, 1)
  try {
    await deleteUnderstanding(note.id)
  } catch {
    const now = data.value?.profile.understanding
    if (now && !now.some((n) => n.id === note.id)) now.splice(Math.min(at, now.length), 0, note)
    toast.error(t('users.profile.notes.deleteFailed'))
  }
}

// ── 一句话里那个人的去处 ─────────────────────────────────────────────────────
// 和 composables/useUserRef 同一套判断：给了 projectId 就用它，没给（undefined）就
// 取当前路由上的 projectId。展示件 UserRef 那边只认 props 里的 to。
function userTo(handle?: string | null, projectId?: string | null): UserRefTarget | null {
  if (!handle) return null
  const pid = projectId !== undefined ? projectId : (route.params.projectId as string | undefined)
  return userRefRoute(handle, pid)
}
function navigate(target: UserRefTarget | null) {
  if (target) void router.push(target)
}
</script>

<template>
  <ProfileViewView
    :handle="handle"
    :project-id="projectId"
    :profile="profile"
    :member="member"
    :recent="data?.recent ?? null"
    :loading="loading"
    :error="!!error"
    :avatar-url="avatarUrl"
    :selected-week="selectedWeek"
    :week-topics="weekTopics"
    :week-pending="weekPending"
    :week-failed="weekFailed"
    :user-to="userTo"
    @refresh="refresh"
    @select-week="pickWeek"
    @clear-week="clearWeek"
    @forget="forget"
    @navigate="navigate"
  />
</template>
