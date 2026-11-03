/**
 * 发题 / 改题时这道题带的材料：选中即上传，名单在这里。
 *
 * 往哪传由调用方给：发题时先传成游离的附件（`POST /attachments`），建题那条请求再带上
 * 它们的 id —— 题目建好之前还没有题目资料库可放，建题时后端把它们拷进去（后端
 * `package.add_uploads`）；改题时直接放进这道题的资料库、在那里替换和删掉，认的是名字。
 *
 * 选中即传的代价是用户中途放弃会留下一个没人引用的对象；换来的是大文件的上传早于提交
 * 发生，用户在表单上就看得见它失败了，而不是点了发布才发现。正在上传时 `uploading`
 * 举着，页面据此挡一下提交：宁可让人等一秒，也不能把「已经选好的材料」静默丢掉。
 */
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'

import { AttachmentsApi } from '@/api/attachments'
import { uploadPercent, type UploadProgress } from '@/api/partUpload'
import { toast } from '@/lib/toast'

/** 这道题带着的一份材料：发题时认附件 id，改题时认它在题目资料库里的名字。 */
export interface PickedAttachment<Id extends number | string = number | string> {
  id: Id
  name: string
  size: number
}

export interface AttachmentTarget<Id extends number | string> {
  /** 传一份，拿回它的 id；传得出进度的（分段上传）边传边报 `progress`。 */
  upload: (file: File, progress: UploadProgress) => Promise<Id>
  /** 从这道题上摘下一份；不给就只是不再带上它。 */
  remove?: (id: Id) => Promise<void>
  /** 把一份换成新的一版，名字不变；不给就不能替换。 */
  replace?: (id: Id, file: File, progress: UploadProgress) => Promise<void>
}

export function useAttachmentUploads<Id extends number | string>(target: AttachmentTarget<Id>) {
  const { t } = useI18n()

  // shallowRef: the list is always replaced whole, and a deep ref would unwrap the generic id.
  const files = shallowRef<PickedAttachment<Id>[]>([])
  const uploading = ref(false)
  /** 正在传的那一份传到了百分之几；没在传、或传法报不出进度时是 `null`。 */
  const percent = ref<number | null>(null)
  const progress: UploadProgress = (sent, total) => (percent.value = uploadPercent(sent, total))
  /** 单份文件的上限，`null` = 还没问到（问不到就不写这句话，不猜一个数出来）。 */
  const maxFileBytes = ref<number | null>(null)

  // 这句话是建议，不是闸门：拿不到就少说一句，选择与上传照旧，也不弹错。
  onMounted(async () => {
    try {
      const { maxFileBytes: bytes } = await AttachmentsApi.limits()
      maxFileBytes.value = bytes
    } catch {
      maxFileBytes.value = null
    }
  })

  async function add(picked: File[]) {
    uploading.value = true
    try {
      for (const file of picked) {
        try {
          percent.value = null
          const id = await target.upload(file, progress)
          // 认名字的那一边，撞名时服务端拿了下一个 `(n)`：列它最后叫什么。
          const name = typeof id === 'string' ? id : file.name
          files.value = [...files.value, { id, name, size: file.size }]
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
      percent.value = null
    }
  }

  async function remove(id: Id) {
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

  async function replace(id: Id, file: File) {
    if (!target.replace) return
    uploading.value = true
    try {
      await target.replace(id, file, progress)
      files.value = files.value.map((picked) => (picked.id === id ? { ...picked, size: file.size } : picked))
    } catch (error) {
      const name = files.value.find((picked) => picked.id === id)?.name ?? ''
      toast.error(
        t('tasks.attachmentPicker.replaceFailed', {
          name,
          error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
        })
      )
    } finally {
      uploading.value = false
      percent.value = null
    }
  }

  /** 已经挂在这道题上的那几份（改题时读进来）。 */
  function reset(list: PickedAttachment<Id>[]) {
    files.value = list
  }

  const ids = computed(() => files.value.map((file) => file.id))

  return { files, uploading, percent, maxFileBytes, ids, add, remove, replace, reset }
}
