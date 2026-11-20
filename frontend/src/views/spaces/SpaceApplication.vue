<script setup lang="ts">
// 一份新建空间的申请（`/space-applications/:spaceId`）：首页侧栏里还没过审的那一行点进来。
// 取数、重新提交、撤回或删除都在这里；画面在 `SpaceApplicationView.vue`。
//
// 后端没有按编号读一份申请的接口，「我的申请」一次给 50 份，从里面找这一份。只有
// 申请人自己读得到。过审了的申请就是一个普通空间：直接换到那个空间去。
import type { SpaceApplication as Application } from '@/types'

import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import SpaceApplicationView from './SpaceApplicationView.vue'

import { createAvatar } from '@/api/avatars'
import { SpacesApi } from '@/api/spaces'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import { toast } from '@/lib/toast'
import { useDialog } from '@/plugins/dialog'

const route = useRoute()
const router = useRouter()
const dialog = useDialog()

const application = ref<Application | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const busy = ref<'resubmit' | 'withdraw' | null>(null)
const error = ref('')

async function load() {
  const id = Number(route.params.spaceId)
  loading.value = true
  loadError.value = null
  try {
    const found = (await SpacesApi.applications()).items.find((item) => item.id === id) ?? null
    if (found?.reviewStatus === 'APPROVED') {
      void router.replace(spaceEntryRoute(found))
      return
    }
    application.value = found
    if (!found) loadError.value = t('spaces.application.notFound')
  } catch {
    loadError.value = t('spaces.application.loadFailed')
  } finally {
    loading.value = false
  }
}

async function resubmit(payload: { name: string; intro: string; avatarFile?: File }) {
  const current = application.value
  if (!current || busy.value) return
  busy.value = 'resubmit'
  error.value = ''
  try {
    const avatarId = payload.avatarFile ? (await createAvatar(payload.avatarFile)).avatarId : undefined
    const { application: next } = await SpacesApi.resubmit(current.id, {
      name: payload.name.trim(),
      intro: payload.intro.trim(),
      avatarId: avatarId ?? current.avatarId ?? undefined,
    })
    application.value = next
    toast.success(t('spaces.application.resubmitted'))
  } catch {
    error.value = t('spaces.application.resubmitFailed')
  } finally {
    busy.value = null
  }
}

async function withdraw() {
  const current = application.value
  if (!current || busy.value) return
  const pending = current.reviewStatus === 'PENDING'
  const label = t(pending ? 'spaces.application.withdraw' : 'spaces.application.delete')
  const confirmed = await dialog
    .confirm(
      t(pending ? 'spaces.application.confirmWithdraw' : 'spaces.application.confirmDelete', { name: current.name }),
      {
        title: label,
        confirmLabel: label,
        danger: true,
      }
    )
    .wait()
  if (!confirmed) return
  busy.value = 'withdraw'
  try {
    await SpacesApi.del(current.id)
  } catch {
    toast.error(t(pending ? 'spaces.application.withdrawFailed' : 'spaces.application.deleteFailed'))
    busy.value = null
    return
  }
  busy.value = null
  // replace：这份申请已经没了，「返回」不该把人送回它。
  void router.replace({ name: 'inbox' })
}

onMounted(load)
watch(
  () => route.params.spaceId,
  (now, before) => {
    if (now && now !== before) void load()
  }
)
</script>

<template>
  <SpaceApplicationView
    :application="application"
    :loading="loading"
    :load-error="loadError"
    :busy="busy"
    :error="error"
    @retry="load"
    @resubmit="resubmit"
    @withdraw="withdraw"
  />
</template>
