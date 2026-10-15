<script setup lang="ts">
// 小队所有者 / 管理员管理「别人怎么找到、怎么进来」的地方：团队地址（handle，
// `/teams/<handle>`）、小队链接（长期有效，可重置）、加入要不要审批、搜不搜得到。
//
// 这一件只吃数据、往上报动作：几样东西取没取回来、写成了没有、换完地址把浏览器带到哪，
// 都是页面（`Members.vue`）那一层的事。只有地址那一格自己拿着草稿 —— 改到一半的东西不该
// 跟着下一个团队跑，确认时连要保存的那一串一起报上去。
import type { Team, TeamVisibility } from '@/types'
import type { TeamsApi } from '@/api/teams'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  team: Team
  /** 这一份小队链接；还没读回来时是 `null`。 */
  link: TeamsApi.TeamJoinLink | null
  /** 有事在做：这一段的按钮和开关都按不动。 */
  busy: boolean
  /** 上一次没做成的理由，画成一条红条。 */
  error: string
  /** 地址那一格上要说的话（被占用 / 不合法 / 没做成）。 */
  handleError: string
  /** 刚复制过：按钮上的字换成「已复制」。 */
  copied: boolean
}>()

const emit = defineEmits<{
  /** 保存这一串地址（去掉了首尾空白）。 */
  saveHandle: [handle: string]
  /** 换一份新的小队链接，旧的立刻失效。 */
  reset: []
  /** 加入要不要审批。 */
  setApproval: [approval: boolean]
  /** 这个团队在搜索里露不露面。 */
  setVisibility: [visibility: TeamVisibility]
  /** 复制这一条链接。 */
  copy: [url: string]
}>()

const addressPrefix = `${window.location.host}/teams/`
const url = computed(() => (props.link ? `${window.location.origin}/team-invites/${props.link.token}` : ''))

const handle = ref(props.team.handle)
const handleChanged = computed(() => handle.value.trim() !== props.team.handle)
watch(
  () => props.team.handle,
  (current) => (handle.value = current)
)
</script>

<template>
  <v-card flat border rounded="lg" class="pa-4 mb-4">
    <h3 class="t-title mb-4">{{ t('work.teamLink.cardTitle') }}</h3>

    <p class="t-body mb-1">{{ t('work.teamLink.address') }}</p>
    <div class="d-flex flex-wrap align-center ga-2">
      <v-text-field
        v-model="handle"
        autocomplete="off"
        :prefix="addressPrefix"
        :label="t('work.teamLink.address')"
        :hint="t('work.teamLink.addressHint')"
        :error-messages="handleError"
        persistent-hint
        density="compact"
        variant="outlined"
        class="link-field"
      />
      <BaseButton
        kind="primary"
        :disabled="busy || !handleChanged || !handle.trim()"
        @click="emit('saveHandle', handle.trim())"
      >
        {{ t('work.teamLink.save') }}
      </BaseButton>
    </div>

    <p class="t-body mt-4 mb-1">{{ t('work.teamLink.title') }}</p>
    <p class="t-meta c-muted mb-2">{{ t('work.teamLink.description') }}</p>
    <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
    <v-progress-linear v-if="busy && !link" indeterminate :aria-label="t('work.teamLink.loading')" />
    <template v-if="link">
      <div class="d-flex flex-wrap align-center ga-2">
        <v-text-field
          autocomplete="off"
          :model-value="url"
          :label="t('work.teamLink.title')"
          readonly
          hide-details
          density="compact"
          variant="outlined"
          class="link-field"
        />
        <BaseButton kind="primary" :disabled="busy" @click="emit('copy', url)">
          {{ copied ? t('work.teamLink.copied') : t('work.teamLink.copy') }}
        </BaseButton>
        <BaseButton :disabled="busy" @click="emit('reset')">{{ t('work.teamLink.reset') }}</BaseButton>
      </div>
      <p class="t-meta c-muted mt-2">{{ t('work.teamLink.resetHint') }}</p>

      <v-switch
        :model-value="link.approval"
        :label="t('work.teamLink.approval')"
        :disabled="busy"
        color="primary"
        hide-details
        inset
        class="mt-2"
        @update:model-value="(value) => emit('setApproval', !!value)"
      />
      <p class="t-meta c-muted">{{ t('work.teamLink.approvalHint') }}</p>
    </template>

    <p class="t-body mt-4 mb-1">{{ t('work.teamLink.visibility') }}</p>
    <v-radio-group
      :model-value="team.visibility"
      :disabled="busy"
      hide-details
      @update:model-value="(value) => emit('setVisibility', value as TeamVisibility)"
    >
      <v-radio value="public" :label="`${t('work.teamLink.public')} · ${t('work.teamLink.publicHint')}`" />
      <v-radio value="stealth" :label="`${t('work.teamLink.stealth')} · ${t('work.teamLink.stealthHint')}`" />
    </v-radio-group>
  </v-card>
</template>

<style scoped>
.link-field {
  flex: 1 1 280px;
  min-width: 0;
}
</style>
