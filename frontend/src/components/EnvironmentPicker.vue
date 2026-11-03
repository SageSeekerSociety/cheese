<script setup lang="ts">
// Choose a sandbox spec from the API or run directly on one whole machine.
// Sandbox placement is hidden; whole-machine execution requires that device's
// team administrator. The caller decides where to save the emitted choice.
import type { Environment, EnvironmentDevice, SandboxSpec } from '../types/compute'

import { computed, ref, useId, watch } from 'vue'

import { t } from '../i18n'
import { sameEnvironment } from '../lib/environment'
import { responseText } from '../lib/noticeText'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{
  current: Environment | null
  devices: EnvironmentDevice[]
  specs: SandboxSpec[]
  busy?: boolean
}>()
const emit = defineEmits<{ select: [env: Environment]; cancel: [] }>()
// 每个单选框一个 id，标签用 for 指向它。
const uid = `env-${useId()}`

const kind = ref<Environment['kind']>(props.current?.kind ?? 'sandbox')
const spec = ref(props.current?.kind === 'sandbox' ? props.current.spec : 'standard')
const deviceId = ref<string | null>(props.current?.kind === 'self_hosted' ? props.current.device_id : null)
watch(
  () => props.current,
  (env) => {
    kind.value = env?.kind ?? 'sandbox'
    spec.value = env?.kind === 'sandbox' ? env.spec : 'standard'
    deviceId.value = env?.kind === 'self_hosted' ? env.device_id : null
  }
)

const mine = computed(() => props.devices.filter((d) => d.owned))
const picked = computed<Environment | null>(() => {
  if (kind.value === 'sandbox')
    return props.specs.some((s) => s.spec === spec.value) ? { kind: 'sandbox', spec: spec.value } : null
  const device = mine.value.find((d) => d.device_id === deviceId.value)
  return device ? { kind: 'self_hosted', device_id: device.device_id } : null
})
const ready = computed(() => !!picked.value && !sameEnvironment(picked.value, props.current))

function submit() {
  if (ready.value && picked.value) emit('select', picked.value)
}
</script>

<template>
  <div class="env-picker">
    <label :for="`${uid}-sandbox`" class="env-row" data-testid="env-sandbox">
      <input :id="`${uid}-sandbox`" v-model="kind" type="radio" name="env-kind" value="sandbox" :disabled="busy" />
      <span class="env-body">
        <span class="env-label">{{ t('compute.env.sandbox') }}</span>
        <span class="env-hint">{{ t('compute.env.sandboxHint') }}</span>
      </span>
    </label>
    <fieldset v-if="kind === 'sandbox'" class="env-sub">
      <legend class="env-legend">{{ t('compute.env.specHeading') }}</legend>
      <label
        v-for="s in specs"
        :key="s.spec"
        :for="`${uid}-spec-${s.spec}`"
        class="env-row"
        :data-testid="`env-spec-${s.spec}`"
      >
        <input
          :id="`${uid}-spec-${s.spec}`"
          v-model="spec"
          type="radio"
          name="env-spec"
          :value="s.spec"
          :disabled="busy"
        />
        <span class="env-body">
          <span class="env-label">{{ s.label }}</span>
          <span v-if="s.description" class="env-hint">{{ responseText(s, 'description') }}</span>
        </span>
      </label>
    </fieldset>

    <label :for="`${uid}-self_hosted`" class="env-row" data-testid="env-self_hosted">
      <input
        :id="`${uid}-self_hosted`"
        v-model="kind"
        type="radio"
        name="env-kind"
        value="self_hosted"
        :disabled="busy || !mine.length"
      />
      <span class="env-body">
        <span class="env-label">{{ t('compute.env.selfHosted') }}</span>
        <span class="env-hint">{{
          mine.length ? t('compute.env.selfHostedHint') : t('compute.env.noDeviceOfMine')
        }}</span>
      </span>
    </label>
    <fieldset v-if="kind === 'self_hosted'" class="env-sub">
      <legend class="env-legend">{{ t('compute.env.whichDevice') }}</legend>
      <label
        v-for="device in devices"
        :key="device.device_id"
        :for="`${uid}-device-${device.device_id}`"
        class="env-row"
        :data-testid="`env-device-${device.device_id}`"
      >
        <input
          :id="`${uid}-device-${device.device_id}`"
          v-model="deviceId"
          type="radio"
          name="env-device"
          :value="device.device_id"
          :disabled="busy || !device.owned"
        />
        <span class="env-body">
          <span class="env-label">{{ device.name }}</span>
          <span class="env-hint">{{
            device.owned ? t(device.online ? 'compute.env.online' : 'compute.env.offline') : t('compute.env.ownerOnly')
          }}</span>
        </span>
      </label>
    </fieldset>

    <div class="env-actions">
      <BaseButton kind="secondary" size="sm" :disabled="busy" data-testid="env-cancel" @click="emit('cancel')">{{
        t('compute.env.cancel')
      }}</BaseButton>
      <BaseButton
        kind="primary"
        size="sm"
        :disabled="busy || !ready"
        :loading="busy"
        data-testid="env-submit"
        @click="submit"
        >{{ t('compute.env.submit') }}</BaseButton
      >
    </div>
  </div>
</template>

<style scoped>
.env-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.env-sub {
  margin: 0 0 0 24px;
  padding: 0;
  border: 0;
}
.env-legend {
  padding: 8px 8px 4px;
  font-size: 13px;
  font-weight: 600;
}
.env-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border-radius: var(--radius-md);
  font-size: 13px;
  cursor: pointer;
}
.env-row:hover {
  background: var(--fill);
}
.env-row input {
  margin-top: 4px;
}
.env-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.env-hint {
  color: var(--muted);
}
.env-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px;
}
</style>
