'use strict';

Object.defineProperty(exports, '__esModule', { value: true });

var core = require('@tiptap/core');

/**
 * Matches a task item to a - [ ] on input.
 */
const inputRegex = /^\s*(\[([( |x])?\])\s$/;
/**
 * This extension allows you to create task items.
 * @see https://www.tiptap.dev/api/nodes/task-item
 */
const TaskItem = core.Node.create({
    name: 'taskItem',
    addOptions() {
        return {
            nested: false,
            HTMLAttributes: {},
            taskListTypeName: 'taskList',
            a11y: undefined,
        };
    },
    content() {
        return this.options.nested ? 'paragraph block*' : 'paragraph+';
    },
    defining: true,
    addAttributes() {
        return {
            checked: {
                default: false,
                keepOnSplit: false,
                parseHTML: element => {
                    const dataChecked = element.getAttribute('data-checked');
                    return dataChecked === '' || dataChecked === 'true';
                },
                renderHTML: attributes => ({
                    'data-checked': attributes.checked,
                }),
            },
        };
    },
    parseHTML() {
        return [
            {
                tag: `li[data-type="${this.name}"]`,
                priority: 51,
            },
        ];
    },
    renderHTML({ node, HTMLAttributes }) {
        return [
            'li',
            core.mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
                'data-type': this.name,
            }),
            [
                'label',
                [
                    'input',
                    {
                        type: 'checkbox',
                        checked: node.attrs.checked ? 'checked' : null,
                    },
                ],
                ['span'],
            ],
            ['div', 0],
        ];
    },
    addKeyboardShortcuts() {
        const shortcuts = {
            Enter: () => this.editor.commands.splitListItem(this.name),
            'Shift-Tab': () => this.editor.commands.liftListItem(this.name),
        };
        if (!this.options.nested) {
            return shortcuts;
        }
        return {
            ...shortcuts,
            Tab: () => this.editor.commands.sinkListItem(this.name),
        };
    },
    addNodeView() {
        return ({ node, HTMLAttributes, getPos, editor, }) => {
            const listItem = document.createElement('li');
            const checkboxWrapper = document.createElement('label');
            const checkboxStyler = document.createElement('span');
            const checkbox = document.createElement('input');
            const content = document.createElement('div');
            const updateA11Y = () => {
                var _a, _b;
                checkbox.ariaLabel = ((_b = (_a = this.options.a11y) === null || _a === void 0 ? void 0 : _a.checkboxLabel) === null || _b === void 0 ? void 0 : _b.call(_a, node, checkbox.checked))
                    || `Task item checkbox for ${node.textContent || 'empty task item'}`;
            };
            updateA11Y();
            checkboxWrapper.contentEditable = 'false';
            checkbox.type = 'checkbox';
            checkbox.addEventListener('mousedown', event => event.preventDefault());
            checkbox.addEventListener('change', event => {
                // if the editor isn’t editable and we don't have a handler for
                // readonly checks we have to undo the latest change
                if (!editor.isEditable && !this.options.onReadOnlyChecked) {
                    checkbox.checked = !checkbox.checked;
                    return;
                }
                const { checked } = event.target;
                if (editor.isEditable && typeof getPos === 'function') {
                    editor
                        .chain()
                        .focus(undefined, { scrollIntoView: false })
                        .command(({ tr }) => {
                        const position = getPos();
                        if (typeof position !== 'number') {
                            return false;
                        }
                        const currentNode = tr.doc.nodeAt(position);
                        tr.setNodeMarkup(position, undefined, {
                            ...currentNode === null || currentNode === void 0 ? void 0 : currentNode.attrs,
                            checked,
                        });
                        return true;
                    })
                        .run();
                }
                if (!editor.isEditable && this.options.onReadOnlyChecked) {
                    // Reset state if onReadOnlyChecked returns false
                    if (!this.options.onReadOnlyChecked(node, checked)) {
                        checkbox.checked = !checkbox.checked;
                    }
                }
            });
            Object.entries(this.options.HTMLAttributes).forEach(([key, value]) => {
                listItem.setAttribute(key, value);
            });
            listItem.dataset.checked = node.attrs.checked;
            checkbox.checked = node.attrs.checked;
            checkboxWrapper.append(checkbox, checkboxStyler);
            listItem.append(checkboxWrapper, content);
            Object.entries(HTMLAttributes).forEach(([key, value]) => {
                listItem.setAttribute(key, value);
            });
            return {
                dom: listItem,
                contentDOM: content,
                update: updatedNode => {
                    if (updatedNode.type !== this.type) {
                        return false;
                    }
                    listItem.dataset.checked = updatedNode.attrs.checked;
                    checkbox.checked = updatedNode.attrs.checked;
                    updateA11Y();
                    return true;
                },
            };
        };
    },
    addInputRules() {
        return [
            core.wrappingInputRule({
                find: inputRegex,
                type: this.type,
                getAttributes: match => ({
                    checked: match[match.length - 1] === 'x',
                }),
            }),
        ];
    },
});

exports.TaskItem = TaskItem;
exports.default = TaskItem;
exports.inputRegex = inputRegex;
//# sourceMappingURL=index.cjs.map
