/**
 * 发题 / 改题时这道题带的材料：选中即上传，名单在这里。
 *
 * 往哪传由调用方给：发题时先传成游离的附件（`POST /attachments`），建题那条请求再带上
 * 它们的 id —— 文件要先在服务端有个 id，建题才挂得上（见后端
 * `TaskAttachmentService.attach_uploaded`）；改题时直接传到这道题上、从这道题上摘。
 *
 * 选中即传的代价是用户中途放弃会留下一个没人引用的对象；换来的是大文件的上传早于提交
 * 发生，用户在表单上就看得见它失败了，而不是点了发布才发现。正在上传时 `uploading`
 * 举着，页面据此挡一下提交：宁可让人等一秒，也不能把「已经选好的材料」静默丢掉。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { AttachmentsApi } from '@/network/api/attachments'

/** 这道题带着的一份材料。 */
export interface PickedAttachment {
  id: number
  name: string
  size: number
}

export interface AttachmentTarget {
  /** 传一份，拿回它的 id。 */
  upload: (file: File) => Promise<number>
  /** 从这道题上摘下一份；不给就只是不再带上它。 */
  remove?: (id: number) => Promise<void>
}

export function useAttachmentUploads(target: AttachmentTarget) {
  const { t } = useI18n()

  const files = ref<PickedAttachment[]>([])
  const uploading = ref(false)
  /** 单份文件的上限，`null` = 还没问到（问不到就不写这句话，不猜一个数出来）。 */
  const maxFileBytes = ref<number | null>(null)

  // 这句话是建议，不是闸门：拿不到就少说一句，选择与上传照旧，也不弹错。
  onMounted(async () => {
    try {
      const { data } = await AttachmentsApi.limits()
      maxFileBytes.value = data.maxFileBytes
    } catch {
      maxFileBytes.value = null
    }
  })

  async function add(picked: File[]) {
    uploading.value = true
    try {
      for (const file of picked) {
        try {
          const id = await target.upload(file)
          files.value = [...files.value, { id, name: file.name, size: file.size }]
        } catch (error) {
          toast.error(
            t('tasks.attachmentPicker.uploadFailed', {
              name: file.name,
              error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
            })
          )
        }
      }
    } finally {
      uploading.value = false
    }
  }

  async function remove(id: number) {
    if (target.remove) {
      try {
        await target.remove(id)
      } catch (error) {
        const name = files.value.find((file) => file.id === id)?.name ?? ''
        toast.error(
          t('tasks.attachmentPicker.removeFailed', {
            name,
            error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
          })
        )
        return
      }
    }
    files.value = files.value.filter((file) => file.id !== id)
  }

  /** 已经挂在这道题上的那几份（改题时读进来）。 */
  function reset(list: PickedAttachment[]) {
    files.value = list
  }

  const ids = computed(() => files.value.map((file) => file.id))

  return { files, uploading, maxFileBytes, ids, add, remove, reset }
}
