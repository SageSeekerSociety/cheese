<script setup lang="ts">
// 空间加入链接的落地页（`/space-invites/:token`）：先看到是哪个空间、怎么加入，按下确认
// 才加入；空间开着「加入需要批准」时，按下去提交的是一份申请。没登录的人到不了这里：
// 路由上的 `spaceSignInGuard` 先把他送去登录，登录完回到这条地址。
//
// 这一页是容器：读令牌、问后端、加入成了带人进空间。画面在 `SpaceInviteViewView.vue`。
import type { SpaceInvite } from '@/types'

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import SpaceInviteViewView from './SpaceInviteViewView.vue'

import { ApiError } from '@/api'
import { SpacesApi } from '@/api/spaces'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'

const props = defineProps<{ token: string }>()

const router = useRouter()
const invite = ref<SpaceInvite | null>(null)
const loading = ref(false)
const loadError = ref('')
const invalid = ref(false)
const joining = ref(false)
const joinError = ref('')
let loadSequence = 0

async function load() {
  const sequence = ++loadSequence
  invite.value = null
  loadError.value = ''
  joinError.value = ''
  invalid.value = false
  loading.value = true
  try {
    const result = await SpacesApi.detailByJoinLink(String(props.token))
    if (sequence === loadSequence) invite.value = result
  } catch (e) {
    if (sequence !== loadSequence) return
    // 重置过的链接、删掉的空间都答 404：和打开一个不存在的东西一样，说这条链接不能用了。
    invalid.value = e instanceof ApiError && e.status === 404
    loadError.value = invalid.value ? t('spaces.join.invalid') : t('spaces.join.loadFailed')
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

async function join() {
  if (joining.value) return
  joining.value = true
  joinError.value = ''
  try {
    const result = await SpacesApi.joinByJoinLink(String(props.token))
    invite.value = result
    // 成了成员就直接进去：留在这一页等于什么都没发生。待批准的留在这里，画面说清在等谁。
    if (result.joinStatus === 'member') await router.push(spaceEntryRoute(result.space))
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      // 看的时候还在、按下去时已经被重置了。
      invite.value = null
      invalid.value = true
      loadError.value = t('spaces.join.invalid')
    } else {
      joinError.value = t('spaces.join.failed')
    }
  } finally {
    joining.value = false
  }
}

watch(() => props.token, load, { immediate: true })
</script>

<template>
  <SpaceInviteViewView
    :invite="invite"
    :loading="loading"
    :load-error="loadError"
    :invalid="invalid"
    :joining="joining"
    :join-error="joinError"
    @join="join"
    @retry="load"
  />
</template>
