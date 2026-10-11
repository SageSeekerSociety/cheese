<script setup lang="ts">
// 邀请码：看得见、改得动、收得回。只对所有者与管理员开放（接口也这么把关）。
//
// 这一层是「落网」的那一半：读列表、改、撤、建，以及点人名去哪。画面那一半在
// InviteCodesView.vue —— 它只认 props、发 emit，因此能在没连 API 的树里单独渲染。
// 两处刻意的做法（调整是「把这一行改成这个样子」、撤销点两下）见那个文件的注释。
//
// 「当前使用中的码」不是「列表里第一张」：判据在 `model.ts`（`currentInviteCode`），
// 由画面那一半算 —— 它只读列表，不落网。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceInviteCode } from '@/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import InviteCodesView from './InviteCodesView.vue'

import { userRefRoute } from '@/lib/userRef'
import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const spaceId = Number(route.params.spaceId)

const codes = ref<SpaceInviteCode[]>([])
const loading = ref(false)
// 读失败和「还没有邀请码」是两件事：失败留在页面上，空列表才交给列表自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const busy = ref(false)
const copiedId = ref<number | null>(null)

/** 正在被调整的那一行；null = 没有人在调整。 */
const editingId = ref<number | null>(null)
const editMaxUses = ref('')
const editExpiresOn = ref('')
const editNote = ref('')

/** 已经点过一下撤销、正在等第二下的那一行。 */
const confirmingId = ref<number | null>(null)

const newOpen = ref(false)
const newMaxUses = ref('50')
const newExpiresOn = ref('')
const newNote = ref('')

/** 「谁建的码」那一颗的去处。画那一颗的是画面那一半（它只认 props），
 *  所以名字与去处在这里算好当 prop 传进去 —— 和原来那颗 chip 的去处一致：
 *  在项目里就去项目里的成员页，项目外去个人主页。 */
function userRefTo(item: SpaceInviteCode): UserRefTarget | null {
  const handle = item.createdBy?.username
  if (!handle) return null
  return userRefRoute(handle, route.params.projectId as string | undefined)
}

function navigateRef(target: UserRefTarget | null) {
  if (target) void router.push(target)
}

/** 表单里那份说明，发出去之前的归一：全是空白 = 没有说明（后端也这么读）。 */
function noteFrom(value: string): string | null {
  return value.trim() || null
}

async function refresh() {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const res = await SpacesApi.listInviteCodes(spaceId)
    codes.value = res.data.inviteCodes ?? []
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
    editingId.value = null
    confirmingId.value = null
  }
}

onMounted(refresh)

async function saveEdit(item: SpaceInviteCode) {
  if (busy.value) return
  const maxUses = Number(editMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesInvalid'))
    return
  }
  // 后端也挡这一条（会 400），先在本地说明白：把人数调到比已经用掉的还少，码当场就废了。
  if (maxUses < item.useCount) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesBelowUsed', { n: item.useCount }))
    return
  }
  busy.value = true
  try {
    await SpacesApi.updateInviteCode(spaceId, item.id, {
      maxUses,
      // 留空 = 永不过期，所以这里发的是显式的 null（后端把「不发」读作「别动这一项」）。
      expiresAt: editExpiresOn.value ? dayjs(editExpiresOn.value).endOf('day').valueOf() : null,
      // 说明同理：表单带着当前值，所以发出去的就是「这一行现在的说明」；清空 = 清掉。
      note: noteFrom(editNote.value),
    })
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.updated'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.updateFailed'))
  } finally {
    busy.value = false
  }
}

async function revoke(item: SpaceInviteCode) {
  if (busy.value) return
  busy.value = true
  try {
    await SpacesApi.revokeInviteCode(spaceId, item.id)
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.revoked'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.revokeFailed'))
  } finally {
    busy.value = false
  }
}

async function submitCreate() {
  if (busy.value) return
  const maxUses = Number(newMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error(t('spaces.inviteCodes.toast.maxUsesInvalid'))
    return
  }
  busy.value = true
  try {
    await SpacesApi.createInviteCode(spaceId, {
      maxUses,
      // `type="date"` 只给到日，取当天的最后一刻 —— 否则选「今天」当场就是过期的。
      ...(newExpiresOn.value ? { expiresAt: dayjs(newExpiresOn.value).endOf('day').valueOf() } : {}),
      note: noteFrom(newNote.value),
    })
    newOpen.value = false
    newExpiresOn.value = ''
    newNote.value = ''
    await refresh()
    toast.success(t('spaces.inviteCodes.toast.created'))
  } catch {
    toast.error(t('spaces.inviteCodes.toast.createFailed'))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <InviteCodesView
    v-model:confirming-id="confirmingId"
    v-model:copied-id="copiedId"
    v-model:edit-expires-on="editExpiresOn"
    v-model:edit-max-uses="editMaxUses"
    v-model:edit-note="editNote"
    v-model:editing-id="editingId"
    v-model:new-expires-on="newExpiresOn"
    v-model:new-max-uses="newMaxUses"
    v-model:new-note="newNote"
    v-model:new-open="newOpen"
    :busy="busy"
    :codes="codes"
    :error-detail="errorDetail"
    :failed="failed"
    :loading="loading"
    :user-ref-to="userRefTo"
    @create="submitCreate"
    @navigate-ref="navigateRef"
    @refresh="refresh"
    @revoke="revoke"
    @save-edit="saveEdit"
  />
</template>
