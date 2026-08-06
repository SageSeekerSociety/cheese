import { Component } from 'vue';
import { ComponentOptionsMixin } from 'vue';
import type { ComponentProps } from 'vue-component-type-helpers';
import { ComponentProvideOptions } from 'vue';
import { DefineComponent } from 'vue';
import type { ExternalToast } from 'vue-sonner';
import { PublicProps } from 'vue';
import { ToasterProps } from 'vue-sonner';
import { ToastT } from 'vue-sonner';
import { ToastToDismiss } from 'vue-sonner';
import type { VAvatar } from 'vuetify/components';
import type { VBtn } from 'vuetify/components';
import type { VCard } from 'vuetify/components';
import type { VCardActions } from 'vuetify/components';
import type { VCardText } from 'vuetify/components';
import type { VIcon } from 'vuetify/components';
import type { VProgressLinear } from 'vuetify/components';

declare type Options = Omit<ToastProps, 'text'> & Pick<ExternalToast, 'duration' | 'onAutoClose' | 'onDismiss' | 'id' | 'important'>;

declare type Props = Omit<ToasterProps, 'richColors' | 'theme' | 'closeButton' | 'className' | 'style'>;

export declare const toast: typeof toastFunction & {
    success: (text: string, options?: Options) => string | number;
    error: (text: string, options?: Options) => string | number;
    warning: (text: string, options?: Options) => string | number;
    info: (text: string, options?: Options) => string | number;
    primary: (text: string, options?: Options) => string | number;
    secondary: (text: string, options?: Options) => string | number;
    dismiss(toastId?: number | string): string | number | undefined;
    toastOriginal: ((message: string | Component | (() => string | Component), data?: ExternalToast) => string | number) & {
        success: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
        info: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
        warning: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
        error: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
        custom: (component: Component, data?: ExternalToast) => string | number;
        message: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
        promise: <ToastData>(promise: Promise<ToastData> | (() => Promise<ToastData>), data?: (Omit<ToastT<Component>, "title" | "type" | "id" | "promise" | "delete"> & {
            id?: number | string;
        } & {
            loading?: string | Component;
            success?: (string | Component | ((data: ToastData) => Component | string | Promise<Component | string>)) | undefined;
            error?: string | Component | ((data: any) => Component | string | Promise<Component | string>);
            description?: string | Component | ((data: any) => Component | string | Promise<Component | string>);
            finally?: () => void | Promise<void>;
        }) | undefined) => (string & {
            unwrap: () => Promise<ToastData>;
        }) | (number & {
            unwrap: () => Promise<ToastData>;
        }) | {
            unwrap: () => Promise<ToastData>;
        } | undefined;
        dismiss: (id?: number | string) => string | number | undefined;
        loading: (message: string | Component | (() => string | Component), data?: ExternalToast) => string | number;
    } & {
        getHistory: () => (ToastT<Component> | ToastToDismiss)[];
    };
};

declare function toastFunction(text: string, options?: Options): string | number;

declare interface ToastProps {
    text: string;
    description?: string;
    vertical?: boolean;
    cardProps?: ComponentProps<typeof VCard>;
    cardTextProps?: ComponentProps<typeof VCardText>;
    cardActionsProps?: ComponentProps<typeof VCardActions>;
    action?: {
        label?: string;
        onClick?: () => void;
        buttonProps?: ComponentProps<typeof VBtn>;
    };
    prependIcon?: string;
    prependIconProps?: ComponentProps<typeof VIcon>;
    avatar?: string;
    multipleAvatars?: string[];
    avatarProps?: ComponentProps<typeof VAvatar>;
    progressBar?: boolean;
    reverseProgressBar?: boolean;
    progressDuration?: number;
    progressBarProps?: ComponentProps<typeof VProgressLinear>;
    loading?: boolean;
}

export declare const VSonner: DefineComponent<Props, {}, {}, {}, {}, ComponentOptionsMixin, ComponentOptionsMixin, {}, string, PublicProps, Readonly<Props> & Readonly<{}>, {
position: "top-left" | "top-right" | "bottom-left" | "bottom-right" | "top-center" | "bottom-center";
hotkey: string[];
expand: boolean;
visibleToasts: number;
offset: string | number;
}, {}, {}, {}, string, ComponentProvideOptions, false, {}, any>;

export { }
