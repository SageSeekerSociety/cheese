<!--
  The rendering half of SpaceInviteView.vue: props in, events out. The page keeps the
  token, the requests and where a successful join goes.
-->

<script setup lang="ts">
import type { SpaceInvite } from '@/types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'

const props = defineProps<{
  /** 链接指向的空间和我在哪一步；还没读回来、或读不到时是 `null`。 */
  invite: SpaceInvite | null
  loading: boolean
  /** 读不到这条链接的那一句（失效时是「已失效或已重置」）。 */
  loadError: string
  /** 链接本身不能用了（404）：没有重试可言。 */
  invalid: boolean
  /** 正在加入。 */
  joining: boolean
  /** 按了加入但没成的那一句。 */
  joinError: string
}>()

const emit = defineEmits<{
  /** 确认加入（开着批准时是提交申请）。 */
  join: []
  /** 再读一次这条链接。 */
  retry: []
}>()

const space = computed(() => props.invite?.space ?? null)
</script>

<template>
  <v-container class="fill-height justify-center pa-4" fluid>
    <v-card class="pa-6" max-width="560" width="100%" rounded="lg" flat border>
      <div class="t-eyebrow c-muted mb-4">{{ t('spaces.join.eyebrow') }}</div>

      <template v-if="invite && space">
        <div class="d-flex align-center mb-4">
          <UserAvatar
            kind="org"
            :avatar="getAvatarUrl(space.avatarId)"
            :name="space.name"
            :seed="String(space.id)"
            size="56"
          />
          <h1 class="t-page-title ml-4" data-user-content>{{ space.name }}</h1>
        </div>
        <p v-if="space.intro" class="t-body mb-6" data-user-content>{{ space.intro }}</p>

        <template v-if="invite.joinStatus === 'member'">
          <p class="t-body c-muted mb-4">{{ t('spaces.join.member') }}</p>
          <BaseButton kind="primary" :to="spaceEntryRoute(space)">{{ t('spaces.join.enter') }}</BaseButton>
        </template>
        <p v-else-if="invite.joinStatus === 'pending'" class="t-body c-muted">{{ t('spaces.join.pending') }}</p>
        <template v-else>
          <p class="t-body c-muted mb-4">
            {{ invite.joinApproval ? t('spaces.join.applyHint') : t('spaces.join.joinHint') }}
          </p>
          <p v-if="joinError" role="alert" class="t-body invite__error mb-4">{{ joinError }}</p>
          <BaseButton kind="primary" :loading="joining" @click="emit('join')">
            {{ invite.joinApproval ? t('spaces.join.apply') : t('spaces.join.join') }}
          </BaseButton>
        </template>
      </template>

      <template v-else>
        <h1 class="t-page-title mb-4">{{ t('spaces.join.title') }}</h1>
        <v-progress-linear v-if="loading" indeterminate :aria-label="t('spaces.join.loading')" />
        <template v-else-if="loadError">
          <p role="alert" class="t-body invite__error mb-4">{{ loadError }}</p>
          <BaseButton v-if="!invalid" kind="secondary" @click="emit('retry')">{{ t('spaces.join.retry') }}</BaseButton>
        </template>
      </template>
    </v-card>
  </v-container>
</template>

<style scoped>
/* Text in the danger colour takes the ink, not the mark (design-system §1.5). */
.invite__error {
  color: var(--danger-ink);
}
</style>
