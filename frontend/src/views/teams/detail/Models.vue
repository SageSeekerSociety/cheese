<script setup lang="ts">
import { ref } from 'vue'

import { useTeamModels } from '@/composables/useTeamModels'

import ModelsView from './ModelsView.vue'

import { t } from '@/i18n'

const {
  teamId,
  connections,
  held,
  personalChoices,
  addPersonal,
  removePersonal,
  platformModels,
  canManage,
  loading,
  error,
  busy,
  saved,
  loginCommand,
  login,
  recheck,
  revoke,
  addHeld,
  checkHeld,
  renameHeld,
  renameDevice,
  removeHeld,
  setModels,
  refresh,
} = useTeamModels()

const addingPersonal = ref(false)
</script>

<template>
  <ModelsView
    :key="teamId"
    v-model:adding-personal="addingPersonal"
    :connections="connections"
    :held="held"
    :personal-choices="personalChoices"
    :platform-models="platformModels"
    :can-manage="canManage"
    :loading="loading"
    :error="error"
    :busy="busy"
    :saved="saved"
    :login-command="loginCommand"
    @login="login"
    @add-personal="addPersonal"
    @remove-personal="removePersonal"
    @recheck="recheck"
    @revoke="revoke"
    @add-held="addHeld"
    @check-held="checkHeld"
    @rename-held="renameHeld"
    @rename-device="renameDevice"
    @remove-held="removeHeld"
    @set-models="setModels"
    @refresh="refresh"
  />
</template>
