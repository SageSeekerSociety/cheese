import { IconsOptions } from '../../../../constants/icons';
import { ButtonViewReturnComponentProps } from '../../../../type';
import { Editor } from '@tiptap/vue-3';
import { PropType } from 'vue';
export declare const actionButtonProps: {
    readonly editor: {
        readonly type: PropType<Editor>;
        readonly required: true;
    };
    readonly icon: {
        readonly type: PropType<keyof IconsOptions>;
        readonly default: undefined;
    };
    readonly tooltip: {
        readonly type: StringConstructor;
        readonly default: "";
    };
    readonly disabled: {
        readonly type: BooleanConstructor;
        readonly default: false;
    };
    readonly color: {
        readonly type: StringConstructor;
        readonly default: undefined;
    };
    readonly action: {
        readonly type: PropType<ButtonViewReturnComponentProps["action"]>;
        readonly default: undefined;
    };
    readonly isActive: {
        readonly type: PropType<ButtonViewReturnComponentProps["isActive"]>;
        readonly default: undefined;
    };
};
export declare const extActionButtonProps: {
    readonly editor: {
        readonly type: PropType<Editor>;
        readonly required: true;
    };
    readonly t: {
        readonly type: PropType<(path: string) => string>;
        readonly required: true;
    };
};
