<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.inviteCodes.title')" color="transparent" density="compact">
      <template #append>
        <v-btn color="primary" prepend-icon="mdi-plus" @click="openCreateDialog">
          {{ t('spaces.inviteCodes.create') }}
        </v-btn>
      </template>
    </v-toolbar>

    <!-- 一段大白话：这一页是给创建者/管理员看的，他要知道码发出去之后别人怎么用。 -->
    <v-alert type="info" variant="tonal" density="comfortable" class="mx-4 mb-4">
      {{ t('spaces.inviteCodes.help') }}
    </v-alert>

    <div v-if="loading" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-list v-else-if="inviteCodes.length > 0" rounded="lg">
      <v-list-item v-for="item in inviteCodes" :key="item.id">
        <template #prepend>
          <v-avatar color="primary-lighten-5" size="42" class="me-3">
            <v-icon color="primary">mdi-ticket-confirmation-outline</v-icon>
          </v-avatar>
        </template>
        <v-list-item-title class="d-flex flex-wrap align-center ga-2">
          <span class="invite-code-text">{{ item.code }}</span>
          <v-btn
            :icon="copiedId === item.id ? 'mdi-check' : 'mdi-content-copy'"
            size="small"
            variant="text"
            :title="t('spaces.inviteCodes.copy')"
            @click="copyCode(item)"
          ></v-btn>
          <v-chip :color="statusOf(item).color" size="small" variant="tonal">
            {{ t(statusOf(item).labelKey) }}
          </v-chip>
        </v-list-item-title>
        <v-list-item-subtitle>
          {{ t('spaces.inviteCodes.usage', { used: item.useCount, total: item.maxUses }) }}
          ·
          {{
            item.expiresAt
              ? t('spaces.inviteCodes.expiresAt', { date: formatDate(item.expiresAt) })
              : t('spaces.inviteCodes.neverExpires')
          }}
          · {{ t('spaces.inviteCodes.createdAt', { date: formatDate(item.createdAt) }) }}
        </v-list-item-subtitle>
      </v-list-item>
    </v-list>

    <!-- 空态要能自己回到「生成」那一步：老题目版一条码都没有，只有一句
         「暂无」会让人以为功能坏了。 -->
    <v-sheet v-else class="pa-6 text-center">
      <p class="text-medium-emphasis mb-3">{{ t('spaces.inviteCodes.empty') }}</p>
      <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreateDialog">
        {{ t('spaces.inviteCodes.create') }}
      </v-btn>
    </v-sheet>

    <v-dialog v-model="dialogOpen" max-width="480">
      <v-card>
        <v-card-title>{{ t('spaces.inviteCodes.create') }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="maxUsesInput"
            autocomplete="off"
            type="number"
            min="1"
            :label="t('spaces.inviteCodes.maxUses')"
            :hint="t('spaces.inviteCodes.maxUsesHint')"
            persistent-hint
            class="mb-6"
          ></v-text-field>
          <v-text-field
            v-model="expiresOnInput"
            autocomplete="off"
            type="date"
            :label="t('spaces.inviteCodes.expiresOn')"
            :hint="t('spaces.inviteCodes.expiresOnHint')"
            persistent-hint
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" :disabled="creating" @click="dialogOpen = false">
            {{ t('spaces.inviteCodes.cancel') }}
          </v-btn>
          <v-btn color="primary" variant="flat" :loading="creating" @click="submitCreate">
            {{ t('spaces.inviteCodes.create') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-sheet>
</template>

<script lang="ts" setup>
import type { PostSpaceInviteCodeRequestData } from '@/network/api/spaces/types'
import type { SpaceInviteCode } from '@/types/spaces'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const route = useRoute()
const spaceId = Number(route.params.spaceId)

const inviteCodes = ref<SpaceInviteCode[]>([])
const loading = ref(false)
const creating = ref(false)
const dialogOpen = ref(false)
const copiedId = ref<number | null>(null)
const maxUsesInput = ref('50')
const expiresOnInput = ref('')

async function load() {
  loading.value = true
  try {
    inviteCodes.value = (await SpacesApi.listInviteCodes(spaceId)).data.inviteCodes
  } catch {
    toast.error(t('spaces.inviteCodes.loadFailed'))
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  maxUsesInput.value = '50'
  expiresOnInput.value = ''
  dialogOpen.value = true
}

/** 状态是算出来的，不是存的：库里只有次数与期限，且两者都能让一张码失效。 */
function statusOf(item: SpaceInviteCode) {
  if (item.expiresAt !== null && item.expiresAt <= Date.now()) {
    return { labelKey: 'spaces.inviteCodes.statusExpired', color: 'warning' }
  }
  if (item.useCount >= item.maxUses) {
    return { labelKey: 'spaces.inviteCodes.statusExhausted', color: 'error' }
  }
  return { labelKey: 'spaces.inviteCodes.statusUsable', color: 'success' }
}

function formatDate(ms: number) {
  return dayjs(ms).format('YYYY-MM-DD')
}

async function copyCode(item: SpaceInviteCode) {
  try {
    await navigator.clipboard.writeText(item.code)
    copiedId.value = item.id
    setTimeout(() => (copiedId.value = null), 1600)
  } catch {
    // 剪贴板被拒（非安全上下文 / 没授权）——码还留在屏幕上，手工选中复制即可。
  }
}

async function submitCreate() {
  if (creating.value) return
  const maxUses = Number(maxUsesInput.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error(t('spaces.inviteCodes.maxUsesInvalid'))
    return
  }
  const payload: PostSpaceInviteCodeRequestData = { maxUses }
  if (expiresOnInput.value) {
    // `type="date"` 只给到日，取当天的最后一刻——否则选「今天」当场就是过期的。
    payload.expiresAt = dayjs(expiresOnInput.value).endOf('day').valueOf()
  }
  creating.value = true
  try {
    await SpacesApi.createInviteCode(spaceId, payload)
    dialogOpen.value = false
    await load()
    toast.success(t('spaces.inviteCodes.createSuccess'))
  } catch {
    toast.error(t('spaces.inviteCodes.createFailed'))
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.invite-code-text {
  font-family: var(--font-mono);
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: 0.08em;
}
</style>
