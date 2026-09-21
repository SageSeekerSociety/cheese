<!--
  头像上传区里的三处固定调色板名（占位底的固定深灰 + 两处白字工具类）和下面 .uploader 的
  两处黑色蒙版值，都是**有意保留**的，见 docs/design-system.md §1.2 例外：
  底色本身不随主题变的地方，压在上面的前景色也不该变。
  它是「一张照片 + 一层暗色蒙版 + 白色提示文字」的取景框，两套主题下长得一样。
  和 components/common/AvatarUploader.vue 是同一套写法，要改一起改。
-->
<template>
  <v-card :title="t('users.settings.profile.title')" rounded="lg">
    <template #text>
      <form class="pt-2 pl-5 pr-5" @submit.prevent="submit">
        <v-row>
          <v-col cols="4">
            <v-list-subheader inset>{{ t('users.settings.profile.avatar') }}</v-list-subheader>
            <div class="avatar-upload">
              <v-img
                :src="previewUrl || getAvatarUrl(profile.avatarId)"
                aspect-ratio="1"
                class="rounded-lg avatar"
                rounded="0"
                size="180"
                color="grey-darken-1"
                cover
              />

              <file-select
                v-model="selectedAvatar"
                accept="image/*"
                :max="1"
                class="uploader"
                content-class="uploader-inner"
                @change="handleFileChange"
              >
                <div
                  class="rounded-lg d-flex flex-column align-center justify-center gap-4 pa-4 text-white uploader-inner"
                >
                  <v-icon size="32">mdi-camera</v-icon>
                  <div class="text-body-1 text-white">{{ t('users.settings.profile.uploadAvatar') }}</div>
                </div>
              </file-select>
            </div>
          </v-col>
          <v-col cols="8">
            <v-list-subheader inset>{{ t('users.settings.profile.nickname') }}</v-list-subheader>
            <v-text-field
              id="field-selectedNickname"
              v-model="selectedNickname"
              autocomplete="nickname"
              name="selectedNickname"
              v-bind="nicknameProps"
            ></v-text-field>
            <v-list-subheader inset>{{ t('users.settings.profile.intro') }}</v-list-subheader>
            <v-text-field v-model="selectedIntro" autocomplete="off" :counter="60" v-bind="introProps"></v-text-field>
          </v-col>
        </v-row>
        <v-row>
          <v-col class="d-flex justify-end gap-4">
            <v-btn @click="handleReset">{{ t('global.reset') }}</v-btn>
            <v-btn color="primary" type="submit">{{ t('global.save') }}</v-btn>
          </v-col>
        </v-row>
      </form>
    </template>
  </v-card>
</template>

<script lang="ts" setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import FileSelect from '@/components/common/FileSelect.vue'
import { AvatarsApi } from '@/network/api/avatars'
import { UserApi } from '@/network/api/users'
import AccountService from '@/services/account'

const { t } = useI18n()
const profile = computed(() => AccountService._user.value!)

const { handleSubmit, defineField, handleReset, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      nickname: z
        .string()
        .trim()
        .min(1, t('users.settings.profile.nicknameRequired'))
        .max(50, t('users.settings.profile.nicknameMax'))
        // 至少有一个汉字、字母或数字，挡住纯符号/纯空白的昵称
        .regex(/[0-9A-Za-z㐀-䶿一-鿿]/, t('users.settings.profile.nicknameCharset')),
      intro: z.string().max(60),
      avatar: z
        .array(
          z
            .instanceof(File)
            .refine((v) => v.size < 2 * 1024 * 1024, { message: t('users.settings.profile.avatarTooLarge') })
        )
        .optional(),
    })
  ),
})

watch(
  profile,
  (newVal) => {
    if (!newVal) return
    resetForm({
      values: {
        nickname: newVal.nickname,
        intro: newVal.intro,
      },
    })
  },
  { immediate: true }
)

const [selectedNickname, nicknameProps] = defineField('nickname', vuetifyConfig)
const [selectedIntro, introProps] = defineField('intro', vuetifyConfig)
const [selectedAvatar, avatarProps] = defineField('avatar', vuetifyConfig)
const previewUrl = ref('')

const handleFileChange = () => {
  previewUrl.value = ''
  const length = selectedAvatar.value?.length ?? 0
  if (length > 0) {
    readAvatar()
  }
}

const readAvatar = () => {
  if (!selectedAvatar.value) return
  if (selectedAvatar.value.length === 0) return
  const file = selectedAvatar.value[0]
  const reader = new FileReader()
  reader.readAsDataURL(file)
  reader.onload = (event) => {
    previewUrl.value = event.target?.result as string
  }
}

const submit = handleSubmit(async (values) => {
  let avatarId = profile.value.avatarId
  if (selectedAvatar.value) {
    try {
      const { data } = await AvatarsApi.createAvatar(selectedAvatar.value[0])
      avatarId = data.avatarId
    } catch (error) {
      toast.error(t('users.settings.profile.uploadAvatarFailed'))
      return
    }
  }
  const submitData = {
    nickname: values.nickname,
    intro: values.intro,
    avatarId: avatarId,
  }
  try {
    await UserApi.updateUserInfo(profile.value.id, submitData)
    toast.success(t('global.updateSuccess'))
    resetForm()
  } catch (error) {
    toast.error(t('global.updateFailed'))
  }
})
</script>

<style scoped lang="scss">
.avatar-upload {
  position: relative;

  .uploader {
    position: absolute;
    top: 0;
    left: 0;
    bottom: 0;
    right: 0;
    /* 蒙版本身：主题无关的取景框，不 token 化（理由见文件顶部注释） */
    border: 2px dashed rgba(0, 0, 0, 0.2);
    border-radius: 8px;
    background-color: rgba(0, 0, 0, 0.5);
  }
}

.uploader-inner {
  height: 100%;
}
</style>
