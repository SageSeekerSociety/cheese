type WaterfallProps = {
    cols: number;
    gap: number | [number, number];
};

/**
 * 瀑布流布局
 *
 * @example
 * const layout = useWaterfall(div, { cols: 4, gap: 4 })
 * layout.mount()
 */
declare const useWaterfall: (el: HTMLElement, props: WaterfallProps) => {
    relayout: () => void;
    mount: () => void;
    unmount: () => void;
};

declare const WaterfallElement_base: new () => {
    $props: WaterfallProps;
    render(): void;
} & WaterfallProps & HTMLElement;
declare class WaterfallElement extends WaterfallElement_base {
    constructor();
    _layout: ReturnType<typeof useWaterfall>;
    connectedCallback(): void;
    disconnectedCallback(): void;
    /**update layout */
    render(): void;
}

export { WaterfallElement, type WaterfallProps };
