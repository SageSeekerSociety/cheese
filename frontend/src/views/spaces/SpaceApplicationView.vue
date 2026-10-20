<script setup lang="ts">
// 我建的一个空间还没过审时，点它看到的就是这一页：待审核时可以撤回，被驳回时看驳回
// 原因、改了重新提交，或者删掉这份申请。
//
// 取数、提交、确认、跳转都在 `SpaceApplication.vue`。这一件只吃 props 出事件（A 档）。
import type { SpaceApplication } from '@/types'

import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 这份申请；没读到（还在读、读失败）时是 null。 */
  application: SpaceApplication | null
  loading?: boolean
  /** 读失败的那一句；null 就是没失败。 */
  loadError?: string | null
  /** 正在做的那件事：按钮转圈，别的按不动。 */
  busy?: 'resubmit' | 'withdraw' | null
  /** 重新提交没成的那一句。 */
  error?: string
}>()
const emit = defineEmits<{
  retry: []
  resubmit: [payload: { name: string; intro: string; avatarFile?: File }]
  /** 撤回（待审核）或删除（已驳回）这份申请：确认那一下由外面做。 */
  withdraw: []
}>()

const name = ref('')
const intro = ref('')
const avatarFile = ref<File>()

// 换了一份申请（或重新提交后读回来），表单从它现在的样子起手。
watch(
  () => props.application,
  (now) => {
    name.value = now?.name ?? ''
    intro.value = now?.intro ?? ''
    avatarFile.value = undefined
  },
  { immediate: true }
)

const rejected = computed(() => props.application?.reviewStatus === 'REJECTED')
const state = computed(() => {
  const now = props.application
  if (!now) return ''
  if (!rejected.value) return t('spaces.application.pendingState')
  return now.reviewedAt
    ? t('spaces.application.rejectedOn', { date: dayjs(now.reviewedAt).format(t('spaces.application.dateFormat')) })
    : t('spaces.application.rejectedState')
})

function resubmit() {
  if (props.busy || !name.value.trim()) return
  emit('resubmit', { name: name.value, intro: intro.value, avatarFile: avatarFile.value })
}
</script>

<template>
  <PageHeader :title="application?.name ?? t('spaces.application.title')" show-on-mobile />
  <div class="space-application">
    <BaseLoadError v-if="loadError" :error="loadError" @retry="emit('retry')" />
    <LoadingSkeleton v-else-if="loading || !application" />
    <template v-else>
      <div class="space-application__state">
        <span class="space-application__tag" :class="`space-application__tag--${application.reviewStatus}`">{{
          t(`home.nav.spaceReview.${application.reviewStatus}`)
        }}</span>
        <span class="t-body">{{ state }}</span>
      </div>

      <template v-if="rejected">
        <section
          v-if="application.reviewReason"
          class="space-application__reason"
          :aria-label="t('spaces.application.reason')"
        >
          <p class="t-meta-read mb-1">{{ t('spaces.application.reason') }}</p>
          <p class="t-body-readable" data-user-content>{{ application.reviewReason }}</p>
        </section>
        <v-form class="space-application__form" @submit.prevent="resubmit">
          <AvatarUploader
            v-model="avatarFile"
            :src="application.avatarId ? getAvatarUrl(application.avatarId) : undefined"
            :disabled="!!busy"
            class="space-application__avatar"
          />
          <v-text-field
            v-model="name"
            autocomplete="off"
            maxlength="255"
            :label="t('spaces.create.name')"
            :disabled="!!busy"
            variant="outlined"
          />
          <v-textarea
            v-model="intro"
            autocomplete="off"
            :label="t('spaces.create.intro')"
            :disabled="!!busy"
            rows="3"
            auto-grow
            variant="outlined"
          />
          <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
        </v-form>
        <div class="space-application__actions">
          <BaseButton
            kind="primary"
            :loading="busy === 'resubmit'"
            :disabled="!name.trim() || (!!busy && busy !== 'resubmit')"
            @click="resubmit"
            >{{ t('spaces.application.resubmit') }}</BaseButton
          >
          <BaseButton
            kind="ghost"
            :loading="busy === 'withdraw'"
            :disabled="!!busy && busy !== 'withdraw'"
            @click="emit('withdraw')"
            >{{ t('spaces.application.delete') }}</BaseButton
          >
        </div>
      </template>

      <template v-else>
        <dl class="space-application__fields">
          <dt>{{ t('spaces.create.name') }}</dt>
          <dd data-user-content>{{ application.name }}</dd>
          <template v-if="application.intro">
            <dt>{{ t('spaces.application.intro') }}</dt>
            <dd data-user-content>{{ application.intro }}</dd>
          </template>
        </dl>
        <div class="space-application__actions">
          <BaseButton kind="secondary" :loading="busy === 'withdraw'" @click="emit('withdraw')">{{
            t('spaces.application.withdraw')
          }}</BaseButton>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.space-application {
  max-width: 640px;
  margin: 0 auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.space-application__state {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--text);
}

/* 和侧栏那一行右边的小标签同一组颜色：待审核是警示那一组，驳回是危险的字色、中性的底。 */
.space-application__tag {
  font-size: 12px;
  line-height: var(--lh-12);
  padding: 0 6px;
  border-radius: var(--radius-sm);
}

.space-application__tag--PENDING {
  background: var(--warn-wash);
  color: var(--warn-ink);
}

.space-application__tag--REJECTED {
  background: var(--fill-2);
  color: var(--danger-ink);
}

.space-application__reason {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
}

.space-application__reason p {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.space-application__form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.space-application__avatar {
  width: 120px;
  margin-bottom: 8px;
}

.space-application__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.space-application__fields {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 12px 24px;
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14-loose);
}

.space-application__fields dt {
  color: var(--muted);
}

.space-application__fields dd {
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
