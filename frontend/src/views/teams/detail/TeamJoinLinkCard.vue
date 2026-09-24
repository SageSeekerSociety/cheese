<script setup lang="ts">
// 小队所有者 / 管理员管理「别人怎么找到、怎么进来」的地方：团队地址（handle，
// `/teams/<handle>`）、小队链接（长期有效，可重置）、加入要不要审批、搜不搜得到。
import type { Team, TeamVisibility } from '@/types'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const props = defineProps<{ team: Team }>()
const emit = defineEmits<{ updated: [team: Team] }>()

const link = ref<TeamsApi.TeamJoinLink | null>(null)
const busy = ref(false)
const error = ref('')
const copied = ref(false)
const url = computed(() => (link.value ? `${window.location.origin}/team-invites/${link.value.token}` : ''))

const route = useRoute()
const router = useRouter()
const addressPrefix = `${window.location.host}/teams/`
const handle = ref(props.team.handle)
const handleError = ref('')
const handleChanged = computed(() => handle.value.trim() !== props.team.handle)
watch(
  () => props.team.handle,
  (current) => (handle.value = current)
)

// A new handle is a new address: the page moves to it, the old one stops working.
async function saveHandle() {
  handleError.value = ''
  busy.value = true
  try {
    const {
      data: { team },
    } = await TeamsApi.update(props.team.id, { handle: handle.value.trim() })
    emit('updated', team)
    await router.replace({
      name: route.name ?? 'TeamsDetailDefault',
      params: { handle: team.handle },
      query: route.query,
    })
  } catch (e) {
    handleError.value =
      e instanceof BusinessError && e.code === 409
        ? t('work.teamLink.handleTaken')
        : e instanceof BusinessError && e.code === 400
          ? t('work.teamLink.handleInvalid')
          : t('work.teamLink.failed')
  } finally {
    busy.value = false
  }
}

async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch {
    error.value = t('work.teamLink.failed')
  } finally {
    busy.value = false
  }
}

watch(
  () => props.team.id,
  (teamId) => {
    link.value = null
    copied.value = false
    void run(async () => {
      const { data } = await TeamsApi.getJoinLink(teamId)
      if (props.team.id === teamId) link.value = data
    })
  },
  { immediate: true }
)

function reset() {
  copied.value = false
  void run(async () => {
    link.value = (await TeamsApi.resetJoinLink(props.team.id)).data
  })
}

function setApproval(approval: boolean | null) {
  void run(async () => {
    link.value = (await TeamsApi.updateJoinLink(props.team.id, { approval: !!approval })).data
  })
}

function setVisibility(visibility: TeamVisibility) {
  void run(async () => {
    const {
      data: { team },
    } = await TeamsApi.update(props.team.id, { visibility })
    emit('updated', team)
  })
}

async function copy() {
  error.value = ''
  try {
    await navigator.clipboard.writeText(url.value)
    copied.value = true
  } catch {
    error.value = t('work.teamLink.copyFailed')
  }
}
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
      <v-btn variant="flat" color="primary" :disabled="busy || !handleChanged || !handle.trim()" @click="saveHandle">
        {{ t('work.teamLink.save') }}
      </v-btn>
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
        <v-btn color="primary" variant="flat" :disabled="busy" @click="copy">
          {{ copied ? t('work.teamLink.copied') : t('work.teamLink.copy') }}
        </v-btn>
        <v-btn variant="text" :disabled="busy" @click="reset">{{ t('work.teamLink.reset') }}</v-btn>
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
        @update:model-value="setApproval"
      />
      <p class="t-meta c-muted">{{ t('work.teamLink.approvalHint') }}</p>
    </template>

    <p class="t-body mt-4 mb-1">{{ t('work.teamLink.visibility') }}</p>
    <v-radio-group
      :model-value="team.visibility"
      :disabled="busy"
      hide-details
      @update:model-value="(value) => setVisibility(value as TeamVisibility)"
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
