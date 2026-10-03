import type { VNode } from 'vue'

import { h, reactive } from 'vue'
import { VTextField } from 'vuetify/lib/components/index.mjs'

import { t } from '@/i18n'

interface DialogOptions<T = any> {
  title: string
  content: string | (() => VNode)
  showCancel?: boolean
  /** 确认键的字（动词）。不给就是「确定」。 */
  confirmLabel?: string
  /** 不可撤销的操作：确认键实心红。 */
  danger?: boolean
  onConfirm?: (value?: any) => T
  onCancel?: () => void
}

interface DialogInstance<T = any> extends DialogOptions<T> {
  id: number
  isOpen: boolean
  wait: () => Promise<T>
}

let nextId = 0

export const dialogs = reactive<DialogInstance[]>([])

const showDialog = <T>(options: DialogOptions<T>): DialogInstance<T> => {
  let resolvePromise!: (value: T) => void
  let rejectPromise!: (reason?: any) => void
  const waitPromise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve
    rejectPromise = reject
  })

  const dialog: DialogInstance<T> = {
    id: nextId++,
    isOpen: true,
    wait: () => waitPromise,
    ...options,
  }

  ;(dialog as any).resolvePromise = resolvePromise
  ;(dialog as any).rejectPromise = rejectPromise
  dialogs.push(dialog)

  return dialog
}

export class CancelError extends Error {
  constructor() {
    super('cancel')
  }
}

export const closeDialog = <T>(id: number, result: T, isCancel = false) => {
  const index = dialogs.findIndex((d) => d.id === id)
  if (index > -1) {
    const dialog = dialogs[index] as DialogInstance<T>
    if (isCancel) {
      ;(dialog as any).rejectPromise(new CancelError())
    } else {
      ;(dialog as any).resolvePromise(result)
    }
    dialogs.splice(index, 1)
  }
}

export function useDialog() {
  const alert = (message: string, options?: { title?: string }): DialogInstance<void> =>
    showDialog({
      title: options?.title || t('global.dialog.alertTitle'),
      content: message,
      showCancel: false,
      onConfirm: () => {},
    })

  const confirm = (
    message: string,
    options?: { title?: string; confirmLabel?: string; danger?: boolean }
  ): DialogInstance<boolean> =>
    showDialog({
      title: options?.title || t('global.confirm'),
      content: message,
      showCancel: true,
      confirmLabel: options?.confirmLabel,
      danger: options?.danger,
      onConfirm: () => true,
      onCancel: () => false,
    })

  const prompt = (
    message: string,
    options?: { title?: string; defaultValue?: string; placeholder?: string; required?: boolean }
  ): DialogInstance<string> => {
    let inputValue = options?.defaultValue || ''
    return showDialog({
      title: options?.title || t('global.dialog.promptTitle'),
      content: () =>
        h(VTextField, {
          modelValue: inputValue,
          placeholder: options?.placeholder,
          label: message,
          required: options?.required,
          'onUpdate:modelValue': (value: string) => {
            inputValue = value
          },
        }),
      showCancel: true,
      onConfirm: () => inputValue,
    })
  }

  const custom = <T>(title: string, content: () => VNode, options?: Partial<DialogOptions<T>>): DialogInstance<T> =>
    showDialog({
      title,
      content,
      showCancel: true,
      ...options,
    })

  return {
    alert,
    confirm,
    prompt,
    custom,
  }
}

export function createDialogPlugin() {
  return {
    install: (app: any) => {
      app.provide('dialogs', dialogs)
      app.provide('closeDialog', closeDialog)
    },
  }
}

export type { DialogInstance, DialogOptions }
