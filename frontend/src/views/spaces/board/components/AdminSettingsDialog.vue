<script setup lang="ts">
// 管理员设置 —— 头部那块下拉里「管理员设置」打开的就是它。
//
// 它是**只读**的一张名单：谁在这块板的管理员名单上、各自是什么角色。原型里这块
// 弹窗就是这个样子（标题 + 一句解释 + 名单 + 一个关闭按钮），一个操作都没有，这里
// 照做 —— 改角色是另一件事，它要有真的写回接口，界面先把「现在是谁」说清楚。
//
// 名单从 `Space.admins` 来（`useI18n` 之外唯一的输入就是 props），所有者也在名单
// 里，所以调用方**不要**再拼一次 `owner`。见 `../model.ts` 的 `Manager`。
import type { Manager } from '../model'

import { useI18n } from 'vue-i18n'

defineProps<{ managers: Manager[] }>()
const open = defineModel<boolean>({ required: true })

const { t } = useI18n()
</script>

<template>
  <v-dialog v-model="open" max-width="520">
    <v-card rounded="lg" class="pa-5">
      <h3 class="text-body-1 font-weight-bold mb-2">{{ t('spaces.adminSettings.title') }}</h3>
      <p class="text-body-2 text-medium-emphasis mb-4">{{ t('spaces.adminSettings.help') }}</p>
      <v-list density="compact">
        <v-list-item v-for="m in managers" :key="m.person.handle" :title="m.person.name">
          <template #subtitle>
            {{ m.role === 'OWNER' ? t('spaces.adminSettings.roleOwner') : t('spaces.adminSettings.roleAdmin') }}
          </template>
        </v-list-item>
      </v-list>
      <div class="text-right">
        <v-btn variant="text" @click="open = false">{{ t('spaces.adminSettings.close') }}</v-btn>
      </div>
    </v-card>
  </v-dialog>
</template>
