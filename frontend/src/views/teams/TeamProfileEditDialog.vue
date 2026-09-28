<script setup lang="ts">
// 小队资料编辑弹窗：侧栏名字旁那支铅笔点开的东西。只管「别人看到的小队」这一层
// —— 名字、介绍、头像。地址（handle）、可见性、加入方式不在这里：它们在成员页的
// 「团队地址与加入」卡上，而且对个人小队根本不成立（后端直接报错），混进来只会
// 多出一组要藏的分支。
import type { Team } from '@/types'

import { ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const props = defineProps<{ team: Team }>()
const emit = defineEmits<{ updated: [team: Team] }>()

const open = defineModel<boolean>()

const name = ref('')
const intro = ref('')
const avatarFile = ref<File>()
const nameError = ref('')
const error = ref('')
const saving = ref(false)

// 每次打开都从小队现在的样子起手：上一次取消留下的半截输入不该跟到下一次，
// 上一次的报错也一样。`immediate` 是为了「打开着被挂上去」也算一次起手。
watch(
  open,
  (isOpen) => {
    if (!isOpen) return
    name.value = props.team.name
    intro.value = props.team.intro ?? ''
    avatarFile.value = undefined
    nameError.value = ''
    error.value = ''
  },
  { immediate: true }
)

const save = async () => {
  nameError.value = ''
  error.value = ''
  const trimmedName = name.value.trim()
  if (!trimmedName) {
    nameError.value = t('work.teamProfile.nameRequired')
    return
  }

  saving.value = true
  try {
    // 换头像 = 先把图传上去换一个 id，再把它和另外两项一起 PATCH 上去；
    // 没换图就不带 avatarId，免得把现成的头像覆盖掉。
    const avatarId = avatarFile.value ? (await AvatarsApi.createAvatar(avatarFile.value)).data.avatarId : null

    const {
      data: { team },
    } = await TeamsApi.update(props.team.id, {
      name: trimmedName,
      intro: intro.value.trim(),
      ...(avatarId ? { avatarId } : {}),
    })

    emit('updated', team)
    open.value = false
    toast.success(t('work.teamProfile.saved'))
  } catch (e) {
    // 名字全站唯一：撞名是 409「Team name already exists」，要落到名字那一格，
    // 别和「没网」「没权限」混成同一句通用报错 —— 用户能自己换一个名字解决它。
    if (e instanceof BusinessError && e.code === 409) {
      nameError.value = t('work.teamProfile.nameTaken')
    } else if (e instanceof BusinessError && e.code === 403) {
      error.value = t('work.teamProfile.forbidden')
    } else {
      error.value = t('work.teamProfile.saveFailed')
      console.error(e)
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-dialog v-model="open" width="600">
    <v-card rounded="lg" class="elevation-0 border">
      <v-toolbar color="transparent" flat>
        <v-toolbar-title class="text-h6">{{ t('work.teamProfile.editTitle') }}</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn icon variant="text" :aria-label="t('work.teamProfile.cancel')" @click="open = false">
          <v-icon>mdi-close</v-icon>
        </v-btn>
      </v-toolbar>

      <v-divider></v-divider>

      <v-card-text class="py-5">
        <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
        <v-container fluid>
          <v-row>
            <v-col cols="12" md="4" class="text-center">
              <avatar-uploader v-model="avatarFile" :src="getAvatarUrl(team.avatarId)" />
              <p class="text-body-2 text-medium-emphasis mb-2">{{ t('work.teamProfile.avatarLabel') }}</p>
            </v-col>
            <v-col cols="12" md="8">
              <v-text-field
                v-model="name"
                autocomplete="off"
                :label="t('work.teamProfile.nameLabel')"
                variant="outlined"
                color="primary"
                :error-messages="nameError"
                class="mb-4"
                rounded="md"
                @update:model-value="nameError = ''"
              ></v-text-field>

              <v-textarea
                v-model="intro"
                autocomplete="off"
                :label="t('work.teamProfile.introLabel')"
                :hint="t('work.teamProfile.introHint')"
                counter="250"
                maxlength="250"
                rows="3"
                auto-grow
                persistent-hint
                variant="outlined"
                color="primary"
                rounded="md"
              ></v-textarea>
            </v-col>
          </v-row>
        </v-container>
      </v-card-text>

      <v-divider></v-divider>

      <v-card-actions class="pa-4">
        <v-spacer></v-spacer>
        <v-btn variant="text" class="mr-2" :disabled="saving" @click="open = false">
          {{ t('work.teamProfile.cancel') }}
        </v-btn>
        <v-btn color="primary" variant="elevated" rounded="md" :loading="saving" @click="save">
          {{ t('work.teamProfile.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
