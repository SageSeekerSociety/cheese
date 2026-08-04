import { PropType } from 'vue';
import { ImageTab, ImageTabKey } from '../../image/types';
declare function __VLS_template(): {
    attrs: Partial<{}>;
    slots: {
        dialog?(_: {
            props: {
                editor: import('@tiptap/vue-3').Editor;
                value: {
                    src?: string | undefined;
                    alt?: string | undefined;
                    title?: string | undefined;
                    lockAspectRatio?: boolean | undefined;
                    width?: (number | string | null) | undefined;
                    height?: (number | string | null) | undefined;
                    display?: import('../../image/types').Display | undefined;
                };
                imageTabs: ImageTab[];
                hiddenTabs: ImageTabKey[];
                upload: (file: File) => Promise<string>;
            };
        }): any;
    };
    refs: {};
    rootEl: any;
};
type __VLS_TemplateResult = ReturnType<typeof __VLS_template>;
declare const __VLS_component: import('vue').DefineComponent<import('vue').ExtractPropTypes<{
    upload: {
        type: PropType<(file: File) => Promise<string>>;
        default: undefined;
    };
    imageTabs: {
        type: PropType<ImageTab[]>;
        default: () => never[];
    };
    hiddenTabs: {
        type: PropType<ImageTabKey[]>;
        default: () => never[];
    };
    editor: {
        readonly type: PropType<import('@tiptap/vue-3').Editor>;
        readonly required: true;
    };
    t: {
        readonly type: PropType<(path: string) => string>;
        readonly required: true;
    };
}>, {}, {}, {}, {}, import('vue').ComponentOptionsMixin, import('vue').ComponentOptionsMixin, {}, string, import('vue').PublicProps, Readonly<import('vue').ExtractPropTypes<{
    upload: {
        type: PropType<(file: File) => Promise<string>>;
        default: undefined;
    };
    imageTabs: {
        type: PropType<ImageTab[]>;
        default: () => never[];
    };
    hiddenTabs: {
        type: PropType<ImageTabKey[]>;
        default: () => never[];
    };
    editor: {
        readonly type: PropType<import('@tiptap/vue-3').Editor>;
        readonly required: true;
    };
    t: {
        readonly type: PropType<(path: string) => string>;
        readonly required: true;
    };
}>> & Readonly<{}>, {
    upload: (file: File) => Promise<string>;
    imageTabs: ImageTab[];
    hiddenTabs: ImageTabKey[];
}, {}, {}, {}, string, import('vue').ComponentProvideOptions, true, {}, any>;
declare const _default: __VLS_WithTemplateSlots<typeof __VLS_component, __VLS_TemplateResult["slots"]>;
export default _default;
type __VLS_WithTemplateSlots<T, S> = T & {
    new (): {
        $slots: S;
    };
};
