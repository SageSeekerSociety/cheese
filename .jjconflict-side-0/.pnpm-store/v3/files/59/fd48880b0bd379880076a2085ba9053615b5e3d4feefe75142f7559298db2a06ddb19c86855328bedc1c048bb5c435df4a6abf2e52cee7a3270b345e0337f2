import { Extension } from '@tiptap/core';
import { PropType } from 'vue';
import { ActionMenuButtonItem } from './types';
export declare const actionButtonMenuProps: {
    maxHeight: {
        type: (NumberConstructor | StringConstructor)[];
        default: undefined;
    };
    items: {
        type: PropType<ActionMenuButtonItem[]>;
        default: () => never[];
    };
    color: {
        readonly type: StringConstructor;
        readonly default: undefined;
    };
    icon: {
        readonly type: PropType<keyof import('../../../../constants/icons').IconsOptions>;
        readonly default: undefined;
    };
    tooltip: {
        readonly type: StringConstructor;
        readonly default: "";
    };
    editor: {
        readonly type: PropType<import('@tiptap/vue-3').Editor>;
        readonly required: true;
    };
    disabled: {
        readonly type: BooleanConstructor;
        readonly default: false;
    };
};
export declare const extActionButtonMenuProps: <T = any>() => {
    readonly extension: {
        readonly type: PropType<Extension<T>>;
        readonly required: true;
    };
    readonly editor: {
        readonly type: PropType<import('@tiptap/vue-3').Editor>;
        readonly required: true;
    };
    readonly t: {
        readonly type: PropType<(path: string) => string>;
        readonly required: true;
    };
};
