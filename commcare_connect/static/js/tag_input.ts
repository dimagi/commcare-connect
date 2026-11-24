/**
 * Turns a textarea that holds one entry per line into a row of tags typed into a single input.
 *
 * The textarea stays in the form, hidden, and is kept in sync (one entry per line), so the
 * server parses the same value it always has.
 *
 *   const tagInput = TagInput.attach(textarea, {
 *     separators: [' ', 'Enter'],  // keys that end an entry; 'Enter' also splits pasted lines
 *     normalize: (value) => value.toLowerCase(),
 *     validate: (value) => value.startsWith('+') || 'Must start with +',
 *     invalidMessage: 'Fix or remove the entries marked in red.',
 *   });
 *   tagInput.valid;  // false while any tag failed `validate`
 *
 * `normalize` rewrites an entry before it becomes a tag. `validate` runs on the normalized entry,
 * when it becomes a tag and after every edit. It returns `true` for a valid entry, or `false` /
 * an error message for an invalid one.
 *
 * Clicking a tag's text, or Backspace in the empty input, edits that tag in place. Enter, a
 * separator or leaving the tag saves the edit, Escape cancels it, and clearing the text removes
 * the tag. Text with separators in it becomes several tags at that position.
 *
 * Ctrl/Cmd+A selects every tag once the input's text is empty or already selected. With the tags
 * selected, Backspace/Delete removes them, copy puts them on the clipboard one per line (so they
 * paste back as tags), and cut does both. Any other key, a click or leaving the field deselects.
 *
 * After every change the textarea fires `tag-input:change` with `detail: {valid, values}`.
 * Duplicates are dropped. Entries never contain line breaks, because a line break separates
 * entries in the stored value.
 *
 * Built-in messages go through Django's JavaScript catalog when the page loads it
 * (`{% url 'javascript-catalog' %}`), and stay in English otherwise.
 *
 * The state and the rules that change it live in `tag_input_state.ts`. This module turns DOM
 * events into those changes, and after each one updates the field and the textarea to match.
 */
import { gettext, interpolate } from './i18n';
import {
  clear,
  commitText,
  editTag,
  finishAll,
  finishEdit,
  initialState,
  isValid,
  paste,
  removeTag,
  separatorPattern,
  setAllSelected,
  setEditText,
  setText,
  storedValue,
  values,
  type FinishEditOptions,
  type Rules,
  type State,
  type Tag,
  type Validate,
} from './tag_input_state';

export interface TagInputConfig {
  /** Keys that end an entry. 'Enter' also splits pasted lines. Defaults to `['Enter']`. */
  separators?: string[];
  normalize?: (value: string) => string;
  validate?: Validate;
  /** Shown under the field while any tag is invalid. */
  invalidMessage?: string;
}

export interface TagInputChangeDetail {
  valid: boolean;
  values: string[];
}

/** The tag input attached to each textarea, so attaching twice returns the same one. */
const instances = new WeakMap<HTMLTextAreaElement, TagInput>();

type Change = (state: State, rules: Rules) => State;

/** The input that replaces a tag's text while it is edited, and which tag that is. */
interface EditElement {
  item: HTMLLIElement;
  input: HTMLInputElement;
  original: string;
}

const MODIFIER_KEYS = ['Shift', 'Control', 'Meta', 'Alt'];

export default class TagInput {
  readonly #textarea: HTMLTextAreaElement;
  readonly #rules: Rules;
  readonly #separators: string[];
  readonly #required: boolean;
  readonly #invalidMessage: string;
  #state: State;

  readonly #container: HTMLDivElement;
  readonly #list: HTMLUListElement;
  readonly #input: HTMLInputElement;
  readonly #error: HTMLParagraphElement;
  /** Rendered tags by value, reused so a click on a tag survives a re-render of the list. */
  readonly #tagElements = new Map<string, HTMLLIElement>();
  #editElement: EditElement | null = null;
  #renderingTags = false;

  static attach(
    textarea: HTMLTextAreaElement,
    config: TagInputConfig = {},
  ): TagInput {
    let tagInput = instances.get(textarea);
    if (!tagInput) {
      tagInput = new TagInput(textarea, config);
      instances.set(textarea, tagInput);
    }
    return tagInput;
  }

  private constructor(textarea: HTMLTextAreaElement, config: TagInputConfig) {
    this.#textarea = textarea;
    this.#separators = config.separators ?? ['Enter'];
    this.#rules = {
      normalize: config.normalize ?? ((value) => value),
      validate: config.validate ?? (() => true),
      pattern: separatorPattern(this.#separators),
    };
    this.#invalidMessage =
      config.invalidMessage ??
      gettext('Fix or remove the entries marked in red.');
    // Read before `hideTextarea` turns it off.
    this.#required = textarea.required;
    this.#state = initialState(this.#rules, textarea.value);

    const inputId = hideTextarea(textarea);
    this.#container = createElement('div', 'tag-input');
    this.#list = createElement('ul', 'tag-input-list');
    this.#input = this.#createMainInput(inputId);
    this.#error = createElement('p', 'tag-input-error');
    this.#error.textContent = this.#invalidMessage;
    this.#container.append(this.#list, this.#input);
    textarea.after(this.#container, this.#error);
    this.#bindContainerEvents();

    this.#render(null);
  }

  get valid(): boolean {
    return isValid(this.#state);
  }

  get values(): string[] {
    return values(this.#state);
  }

  removeTag(value: string): void {
    this.#update((state) => removeTag(state, value));
  }

  clear(): void {
    this.#update(clear);
  }

  editTag(value: string): void {
    this.#update((state, rules) => editTag(state, rules, value));
  }

  // Applying changes.

  #update(change: Change): void {
    const previous = this.#state;
    const next = change(previous, this.#rules);
    if (next === previous) return;
    this.#state = next;
    this.#render(previous);
  }

  /** Brings the field and the textarea up to date with the state. `previous` is null at first. */
  #render(previous: State | null): void {
    const state = this.#state;
    const editStarted =
      state.editing !== null &&
      state.editing.original !== previous?.editing?.original;
    if (
      state.tags !== previous?.tags ||
      state.editing?.index !== previous?.editing?.index ||
      editStarted
    ) {
      this.#renderTags();
    }
    if (this.#input.value !== state.text) this.#input.value = state.text;

    const valid = isValid(state);
    this.#container.classList.toggle('tag-input-has-errors', !valid);
    this.#container.classList.toggle(
      'tag-input-all-selected',
      state.allSelected,
    );
    this.#error.hidden = valid;
    this.#input.setCustomValidity(this.#validityMessage());

    if (state.tags !== previous?.tags || state.text !== previous?.text) {
      this.#syncTextarea();
    }
    // Last, because moving focus can blur the main input, which starts another update.
    if (editStarted) this.#focusEditInput();
  }

  #syncTextarea(): void {
    this.#textarea.value = storedValue(this.#state);
    this.#textarea.dispatchEvent(
      new CustomEvent<TagInputChangeDetail>('tag-input:change', {
        bubbles: true,
        detail: { valid: this.valid, values: this.values },
      }),
    );
  }

  #validityMessage(): string {
    if (!this.valid) return this.#invalidMessage;
    if (this.#required && !storedValue(this.#state)) {
      return gettext('This field is required.');
    }
    return '';
  }

  // Rendering the tags.

  /** Puts one item per tag in the list, reusing the items already rendered for the same values. */
  #renderTags(): void {
    const { tags, editing } = this.#state;
    const items = tags.map((tag, index) =>
      editing?.index === index ? this.#editItem(tag) : this.#tagItem(tag),
    );
    if (!editing) this.#editElement = null;

    const current = new Set(tags.map((tag) => tag.value));
    this.#tagElements.forEach((_, value) => {
      if (!current.has(value)) this.#tagElements.delete(value);
    });

    // Moving a focused element blurs it, so focus is put back once the list is in order.
    const focused = document.activeElement;
    this.#renderingTags = true;
    try {
      // Moves only the items that are out of place, so the others keep pending clicks.
      items.forEach((item, index) => {
        const atIndex = this.#list.children[index] ?? null;
        if (atIndex !== item) this.#list.insertBefore(item, atIndex);
      });
      while (this.#list.children.length > items.length) {
        this.#list.lastElementChild?.remove();
      }
    } finally {
      this.#renderingTags = false;
    }
    if (
      focused instanceof HTMLElement &&
      focused.isConnected &&
      document.activeElement !== focused
    ) {
      focused.focus();
    }
  }

  #tagItem({ value, error }: Tag): HTMLLIElement {
    const existing = this.#tagElements.get(value);
    if (existing) return existing;

    const item = createElement(
      'li',
      error ? 'tag-input-tag tag-input-tag-invalid' : 'tag-input-tag',
    );
    if (error) item.title = error;

    const text = createElement('button', 'tag-input-text');
    text.type = 'button';
    text.textContent = value;
    text.setAttribute('aria-label', interpolate(gettext('Edit %s'), [value]));
    text.addEventListener('click', () => this.editTag(value));
    item.append(text);

    if (error) {
      const description = createElement('span', 'sr-only');
      description.textContent = `(${error})`;
      item.append(description);
    }

    const remove = createElement('button', 'tag-input-remove');
    remove.type = 'button';
    remove.setAttribute(
      'aria-label',
      interpolate(gettext('Remove %s'), [value]),
    );
    const icon = createElement('i', 'fa-solid fa-xmark');
    icon.setAttribute('aria-hidden', 'true');
    remove.append(icon);
    remove.addEventListener('click', () => {
      this.removeTag(value);
      this.#input.focus();
    });
    item.append(remove);

    this.#tagElements.set(value, item);
    return item;
  }

  #editItem(tag: Tag): HTMLLIElement {
    if (this.#editElement?.original === tag.value) {
      return this.#editElement.item;
    }
    const item = createElement('li', 'tag-input-tag tag-input-tag-editing');
    const input = this.#createEditInput(tag.value);
    item.append(input);
    this.#editElement = { item, input, original: tag.value };
    return item;
  }

  #focusEditInput(): void {
    const input = this.#editElement?.input;
    if (!input) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  // The main input.

  #createMainInput(inputId: string | undefined): HTMLInputElement {
    const input = createElement('input', 'tag-input-field');
    input.type = 'text';
    input.placeholder = this.#textarea.placeholder;
    if (inputId) input.id = inputId;
    const describedBy = this.#textarea.getAttribute('aria-describedby');
    if (describedBy) input.setAttribute('aria-describedby', describedBy);

    input.addEventListener('keydown', (event) => this.#onKeydown(event));
    input.addEventListener('input', () =>
      this.#update((state, rules) => setText(state, rules, input.value)),
    );
    input.addEventListener('paste', (event) => {
      event.preventDefault();
      const text = withPasted(input, event);
      this.#update((state, rules) => paste(state, rules, text));
    });
    input.addEventListener('copy', (event) => {
      if (this.#state.allSelected) this.#copyAllTags(event);
    });
    input.addEventListener('cut', (event) => {
      if (!this.#state.allSelected) return;
      this.#copyAllTags(event);
      this.#update(clear);
    });
    input.addEventListener('blur', () =>
      this.#update((state, rules) =>
        setAllSelected(commitText(state, rules), false),
      ),
    );
    return input;
  }

  #onKeydown(event: KeyboardEvent): void {
    if (event.isComposing) return;
    const state = this.#state;
    const withModifier = event.ctrlKey || event.metaKey;

    if (withModifier && event.key.toLowerCase() === 'a') {
      if (canSelectAllTags(this.#input, state)) {
        event.preventDefault();
        this.#update((s) => setAllSelected(s, true));
      }
      return;
    }

    if (state.allSelected) {
      if (event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault();
        // Select-all also covers the typed text: it is either empty or was selected too.
        this.#update(clear);
        return;
      }
      if (!withModifier && !MODIFIER_KEYS.includes(event.key)) {
        this.#update((s) => setAllSelected(s, false));
      }
    }

    if (this.#separators.includes(event.key)) {
      event.preventDefault();
      this.#update(commitText);
      return;
    }

    const lastTag = state.tags.at(-1);
    if (event.key === 'Backspace' && state.text === '' && lastTag) {
      // Without this, the default action deletes the last character of the tag's text.
      event.preventDefault();
      this.editTag(lastTag.value);
    }
  }

  #copyAllTags(event: ClipboardEvent): void {
    event.preventDefault();
    event.clipboardData?.setData('text/plain', this.values.join('\n'));
  }

  #bindContainerEvents(): void {
    this.#container.addEventListener('mousedown', () =>
      this.#update((state) => setAllSelected(state, false)),
    );
    // Clicks on the field's empty space focus the input.
    this.#container.addEventListener('click', (event) => {
      if (event.target === this.#container || event.target === this.#list) {
        this.#input.focus();
      }
    });
    // Saves any typed or half-edited entry before the form reads the textarea. The browser has
    // already validated the form by now, so an entry that turns out invalid stops the submit here.
    this.#textarea.form?.addEventListener(
      'submit',
      (event) => {
        this.#update(finishAll);
        if (!this.valid) {
          // htmx submits from its own listener on the form, so stopping here is what blocks it.
          event.preventDefault();
          event.stopImmediatePropagation();
          this.#input.reportValidity();
        }
      },
      { capture: true },
    );
  }

  // Editing a tag in place.

  #createEditInput(value: string): HTMLInputElement {
    const input = createElement('input', 'tag-input-edit');
    input.type = 'text';
    input.value = value;
    input.setAttribute('aria-label', interpolate(gettext('Edit %s'), [value]));
    const fitToText = () => (input.size = Math.max(input.value.length, 1) + 1);
    fitToText();

    input.addEventListener('input', () => {
      fitToText();
      this.#update((state) => setEditText(state, input.value));
    });
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        // Escape only cancels this edit; it must not also close a modal the field sits in.
        event.stopPropagation();
        this.#finishEditAndLeave({ cancel: true });
      } else if (
        event.key === 'Enter' ||
        this.#separators.includes(event.key)
      ) {
        event.preventDefault();
        this.#finishEditAndLeave();
      }
    });
    // A text input drops pasted line breaks, which would join the lines into one entry, so a
    // paste with line breaks is saved straight away as separate tags.
    input.addEventListener('paste', (event) => {
      if (!/[\r\n]/.test(event.clipboardData?.getData('text') ?? '')) return;
      event.preventDefault();
      this.#finishEditAndLeave({ text: withPasted(input, event) });
    });
    input.addEventListener('blur', () => {
      // Moving or removing this input while the list re-renders blurs it; that isn't the user leaving.
      if (this.#renderingTags) return;
      this.#update((state, rules) => finishEdit(state, rules));
    });
    return input;
  }

  #finishEditAndLeave(options?: FinishEditOptions): void {
    this.#update((state, rules) => finishEdit(state, rules, options));
    // Moving focus blurs the edit input, whose blur handler then finds nothing left to finish.
    this.#input.focus();
  }
}

// DOM helpers.

function createElement<K extends keyof HTMLElementTagNameMap>(
  tagName: K,
  className: string,
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tagName);
  element.className = className;
  return element;
}

/** Hides the textarea and points its labels at the visible input. Returns that input's id. */
function hideTextarea(textarea: HTMLTextAreaElement): string | undefined {
  textarea.hidden = true;
  // A hidden control can't show its own validation message; the visible input reports it instead.
  textarea.required = false;
  if (!textarea.id) return undefined;

  const inputId = `${textarea.id}_tag_input`;
  document
    .querySelectorAll<HTMLLabelElement>(`label[for="${textarea.id}"]`)
    .forEach((label) => {
      label.htmlFor = inputId;
    });
  return inputId;
}

/** Ctrl/Cmd+A selects the tags once the input's text is empty or already all selected. */
function canSelectAllTags(input: HTMLInputElement, state: State): boolean {
  const { value, selectionStart, selectionEnd } = input;
  const textAllSelected = selectionStart === 0 && selectionEnd === value.length;
  return state.tags.length > 0 && (value === '' || textAllSelected);
}

/** The input's text with the pasted text in place of its selection. */
function withPasted(input: HTMLInputElement, event: ClipboardEvent): string {
  const { value, selectionStart, selectionEnd } = input;
  const pasted = event.clipboardData?.getData('text') ?? '';
  return (
    value.slice(0, selectionStart ?? value.length) +
    pasted +
    value.slice(selectionEnd ?? value.length)
  );
}
