<script setup lang="ts">
// 空间设置里「访问范围」这一栏的画面：只对成员开放，还是开放给一组邮箱后缀。
// 读写都不在这里 —— 容器 `Access.vue` 把这块板现在的范围和最近一次申请递进来，
// 接住「保存」去办。
//
// 开放（或扩大范围）要等平台审核，通过前这块板照旧；收回到只对成员开放、或把范围
// 收窄，保存即生效。所以这一页同时画「现在是什么」和「申请走到哪了」两件事。
import type { SpaceOpenRequest } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

export type AccessMode = 'members' | 'open'

export interface AccessChange {
  mode: AccessMode
  scope: string[]
}

const props = defineProps<{
  /** 现在生效的范围；`null` 是只对成员开放。 */
  openScope: string[] | null
  /** 最近一次开放申请；没申请过是 `null`。 */
  openRequest: SpaceOpenRequest | null
  saving: boolean
}>()

const emit = defineEmits<{
  save: [change: AccessChange]
}>()

const { t } = useI18n()

const pending = computed(() => (props.openRequest?.status === 'PENDING' ? props.openRequest : null))
/** 只在最近一次申请被驳回、且之后没有再申请时说驳回原因。 */
const rejected = computed(() => (props.openRequest?.status === 'REJECTED' ? props.openRequest : null))

const mode = ref<AccessMode>('members')
const scopeText = ref('')

// 表单跟着这块板走：有待审的申请时填申请里的范围，否则填现在生效的范围。
watch(
  () => [props.openScope, props.openRequest] as const,
  ([scope, request]) => {
    const shown = request?.status === 'PENDING' ? request.scope : scope
    mode.value = shown ? 'open' : 'members'
    scopeText.value = (shown ?? []).join('\n')
  },
  { immediate: true, deep: true }
)

const scope = computed(() =>
  scopeText.value
    .split(/[\s,，;；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
)

const scopeError = computed(() =>
  mode.value === 'open' && scope.value.length === 0 ? t('spaces.access.scopeRequired') : ''
)

function submit() {
  if (scopeError.value) return
  emit('save', { mode: mode.value, scope: mode.value === 'open' ? scope.value : [] })
}
</script>

<template>
  <p class="settings-page__lede access__lede">{{ t('spaces.access.intro') }}</p>

  <form class="settings-card" novalidate @submit.prevent="submit">
    <div class="srow">
      <span class="srow__k">{{ t('spaces.access.current') }}</span>
      <span class="access__now">
        {{ openScope ? t('spaces.access.nowOpen', { scope: openScope.join('、') }) : t('spaces.access.nowMembers') }}
      </span>
    </div>

    <p v-if="pending" class="access__note" role="status">
      {{ t('spaces.access.pending', { scope: pending.scope.join('、') }) }}
    </p>
    <p v-else-if="rejected" class="access__note access__note--rejected" role="status">
      {{ t('spaces.access.rejected', { reason: rejected.reason ?? '' }) }}
    </p>

    <div class="srow srow--field">
      <span id="space-access-mode-label" class="srow__k">{{ t('spaces.access.mode') }}</span>
      <v-radio-group v-model="mode" aria-labelledby="space-access-mode-label" hide-details density="compact">
        <v-radio value="members">
          <template #label>
            <span class="access__choice">
              <span class="access__choice-name">{{ t('spaces.access.members') }}</span>
              <span class="field-note">{{ t('spaces.access.membersHint') }}</span>
            </span>
          </template>
        </v-radio>
        <v-radio value="open">
          <template #label>
            <span class="access__choice">
              <span class="access__choice-name">{{ t('spaces.access.open') }}</span>
              <span class="field-note">{{ t('spaces.access.openHint') }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
    </div>

    <div v-if="mode === 'open'" class="srow srow--field">
      <label id="space-access-scope-label" class="srow__k srow__k--stack" for="space-access-scope">
        {{ t('spaces.access.scope') }}
        <small class="field-note">{{ t('spaces.access.scopeHint') }}</small>
      </label>
      <v-textarea
        id="space-access-scope"
        v-model="scopeText"
        autocomplete="off"
        variant="outlined"
        density="compact"
        rows="3"
        auto-grow
        :placeholder="t('spaces.access.scopePlaceholder')"
        :error-messages="scopeError"
        hide-details="auto"
      />
    </div>

    <div class="settings-foot">
      <BaseButton kind="primary" :loading="saving" @click="submit">
        {{ t('spaces.access.save') }}
      </BaseButton>
    </div>
  </form>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.access__lede {
  margin: 0 0 12px;
}

/* A row holding a control: the label faces the control's first line, as on
   the basic-info page. */
.srow--field {
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 20px 24px;
}

.srow--field > .srow__k {
  padding-top: 10px;
}

.srow__k--stack {
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.field-note {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}

.access__now {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  overflow-wrap: anywhere;
}

.access__note {
  margin: 0;
  padding: 12px 24px;
  border-bottom: 1px solid var(--line);
  background: var(--fill);
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.access__note--rejected {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.access__choice {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}

.access__choice-name {
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
}

/* Breakpoint matches the shared token (`styles/breakpoints.scss`), the same
   line as `settings-card.css`. */
@media (max-width: 767.98px) {
  .srow--field {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .srow--field > .srow__k {
    padding-top: 0;
  }

  .access__note {
    padding: 12px 16px;
  }
}
</style>
