<script setup lang="ts">
import type { SpaceApplication } from '@/network/api/spaces/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const status = ref('PENDING')
const items = ref<SpaceApplication[]>([])
const loading = ref(false)
const error = ref('')
const offset = ref(0)
const selected = ref<SpaceApplication | null>(null)
const reason = ref('')
const saving = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = (await SpacesApi.reviews(status.value, offset.value)).data.items
  } catch {
    error.value = t('spaces.review.loadFailed')
  } finally {
    loading.value = false
  }
}

function changeStatus() {
  offset.value = 0
  void load()
}

function page(delta: number) {
  offset.value += delta
  void load()
}

function reject(item: SpaceApplication) {
  selected.value = item
  reason.value = ''
}

async function decide(item: SpaceApplication, approved: boolean) {
  if (saving.value || (!approved && !reason.value.trim())) return
  saving.value = true
  error.value = ''
  try {
    await SpacesApi.review(item.id, approved, reason.value.trim())
    selected.value = null
    reason.value = ''
    await load()
  } catch {
    error.value = t('spaces.review.actionFailed')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <v-container fluid>
    <h1 class="text-h5 mb-4">{{ t('spaces.review.title') }}</h1>
    <p class="text-body-2 mb-4">{{ t('spaces.review.adminHelp') }}</p>
    <div class="d-flex align-center mb-4">
      <v-select
        v-model="status"
        autocomplete="off"
        :items="[
          { value: 'PENDING', title: t('spaces.review.PENDING') },
          { value: 'APPROVED', title: t('spaces.review.APPROVED') },
          { value: 'REJECTED', title: t('spaces.review.REJECTED') },
        ]"
        :label="t('spaces.review.status')"
        :disabled="loading || saving"
        hide-details
        @update:model-value="changeStatus"
      />
      <v-btn class="ml-2" variant="text" :loading="loading" :disabled="saving" @click="load">{{
        t('spaces.review.refresh')
      }}</v-btn>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-4" role="alert">{{ error }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <p v-else-if="!items.length && !error" class="text-body-2">{{ t('spaces.review.empty') }}</p>
    <v-card v-for="item in items" :key="item.id" variant="outlined" class="mb-3">
      <v-card-title>{{ item.name }}</v-card-title>
      <v-card-text>
        <p class="text-body-2 mb-2">{{ t('spaces.review.applicant') }}：{{ item.owner }}</p>
        <p class="text-body-2">{{ item.intro }}</p>
        <p v-if="item.description" class="text-body-2 mt-2">{{ item.description }}</p>
        <p v-if="item.reviewReason" class="text-body-2 mt-2">
          {{ t('spaces.review.reason') }}：{{ item.reviewReason }}
        </p>
        <p v-if="item.reviewedBy" class="text-body-2 text-medium-emphasis mt-2">
          {{ item.reviewedBy }} · {{ item.reviewedAt }}
        </p>
      </v-card-text>
      <v-card-actions v-if="item.reviewStatus === 'PENDING'">
        <v-btn color="primary" variant="flat" :disabled="saving" @click="decide(item, true)">{{
          t('spaces.review.approve')
        }}</v-btn>
        <v-btn :disabled="saving" @click="reject(item)">{{ t('spaces.review.reject') }}</v-btn>
      </v-card-actions>
    </v-card>
    <div class="d-flex justify-end">
      <v-btn :disabled="!offset || loading || saving" @click="page(-50)">{{ t('spaces.review.previous') }}</v-btn>
      <v-btn :disabled="items.length < 50 || loading || saving" @click="page(50)">{{ t('spaces.review.next') }}</v-btn>
    </div>
    <v-dialog
      :model-value="!!selected"
      max-width="520"
      :persistent="saving"
      @update:model-value="!$event && (selected = null)"
    >
      <v-card :title="t('spaces.review.reject')">
        <v-card-text>
          <p class="text-body-2 mb-3">{{ selected?.name }}</p>
          <v-textarea v-model="reason" autocomplete="off" :label="t('spaces.review.reason')" :disabled="saving" />
          <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn :disabled="saving" @click="selected = null">{{ t('spaces.create.cancel') }}</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="saving"
            :disabled="!reason.trim() || saving"
            @click="selected && decide(selected, false)"
            >{{ t('spaces.review.reject') }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>
