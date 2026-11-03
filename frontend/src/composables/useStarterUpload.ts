/**
 * 题目的起始代码（#4767）：一份 .zip，选中即上传，现在是哪一份在这里。
 *
 * 往哪传由调用方给，和材料一样（`useAttachmentUploads`）：发题时先传成附件、建题那条
 * 请求带上它的 id；改题时直接设成这道题的起始代码、当场记一版。压缩包合不合格由服务端
 * 当场说，说不行的那一份不会成为「现在这一份」。
 */
import { ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'

import { uploadPercent, type UploadProgress } from '@/api/partUpload'
import { toast } from '@/lib/toast'

export interface StarterFile<Id extends number | string = number | string> {
  id: Id
  name: string
  size: number
}

export interface StarterTarget<Id extends number | string> {
  /** 传一份，拿回它（改题时它的名字是固定的那一个）；传得出进度的边传边报 `progress`。 */
  upload: (file: File, progress: UploadProgress) => Promise<StarterFile<Id>>
  /** 从这道题上摘掉；不给就只是不再带上它。 */
  remove?: () => Promise<void>
}

export function useStarterUpload<Id extends number | string>(target: StarterTarget<Id>) {
  const { t } = useI18n()
  const file = shallowRef<StarterFile<Id> | null>(null)
  const uploading = ref(false)
  /** 传到了百分之几；没在传、或传法报不出进度时是 `null`。 */
  const percent = ref<number | null>(null)

  async function pick(picked: File) {
    uploading.value = true
    try {
      file.value = await target.upload(picked, (sent, total) => (percent.value = uploadPercent(sent, total)))
    } catch (error) {
      toast.error(
        t('tasks.starterPicker.uploadFailed', {
          error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
        })
      )
    } finally {
      uploading.value = false
      percent.value = null
    }
  }

  async function remove() {
    if (target.remove) {
      try {
        await target.remove()
      } catch (error) {
        toast.error(
          t('tasks.starterPicker.removeFailed', {
            error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
          })
        )
        return
      }
    }
    file.value = null
  }

  function reset(value: StarterFile<Id> | null) {
    file.value = value
  }

  return { file, uploading, percent, pick, remove, reset }
}
