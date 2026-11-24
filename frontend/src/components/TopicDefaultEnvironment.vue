<script setup lang="ts">
// 频道详情「设置」里那一行：这个频道里新开始运行的 AI 队友默认用哪个环境。改它不搬
// 已经在运行的；每位队友自己那一条在「成员」那一页它那一行下面。
import { onMounted } from 'vue'

import { useTopicRoster } from '../composables/useTopicRoster'
import { t } from '../i18n'
import { environmentName } from '../lib/environment'

import EnvironmentMenu from './EnvironmentMenu.vue'

const props = defineProps<{ topicId: string }>()

const { machines, machinesError, loadMachines, saveDefault } = useTopicRoster(() => props.topicId)
onMounted(loadMachines)
</script>

<template>
  <div class="default-env" data-testid="future-machine">
    <template v-if="machines">
      <span class="default-env__text">{{
        t('compute.env.defaultHere', {
          name: environmentName(
            machines.default ?? { kind: 'sandbox', spec: 'standard' },
            machines.devices,
            machines.specs
          ),
        })
      }}</span>
      <span v-if="machines.default_from === 'project'" class="default-env__tag">{{
        t('compute.env.projectDefault')
      }}</span>
      <EnvironmentMenu
        :heading="t('compute.env.defaultHeading')"
        :hint="t('compute.env.defaultHint')"
        :current="machines.default"
        :devices="machines.devices"
        :specs="machines.specs"
        :save="saveDefault"
        @changed="loadMachines"
      />
    </template>
    <template v-else-if="machinesError">
      <span class="default-env__text">{{ t('compute.env.loadFailed') }}</span>
      <button type="button" class="default-env__retry" @click="loadMachines">{{ t('global.loadError.retry') }}</button>
    </template>
    <v-progress-circular v-else indeterminate size="14" width="2" />
  </div>
</template>

<style scoped>
.default-env {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.default-env__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.default-env__tag {
  flex: none;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.default-env__retry {
  border: 0;
  background: transparent;
  color: var(--text);
  text-decoration: underline;
  cursor: pointer;
}
</style>
