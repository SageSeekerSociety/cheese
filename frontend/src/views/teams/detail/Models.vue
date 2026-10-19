<script setup lang="ts">
import { useTeamModels } from '@/composables/useTeamModels'

import ModelsView from './ModelsView.vue'

const {
  teamId,
  connections,
  held,
  devices,
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
</script>

<template>
  <ModelsView
    :key="teamId"
    :connections="connections"
    :held="held"
    :devices="devices"
    :platform-models="platformModels"
    :can-manage="canManage"
    :loading="loading"
    :error="error"
    :busy="busy"
    :saved="saved"
    :login-command="loginCommand"
    @login="login"
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
