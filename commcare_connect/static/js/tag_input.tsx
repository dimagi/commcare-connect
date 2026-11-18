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
 * `TagInput` owns the state (see `tag_input_state.ts`) and keeps the textarea in sync with it.
 * The visible field is a React component that renders the state and sends changes back.
 */
import {
  useLayoutEffect,
  useRef,
  useSyncExternalStore,
  type ClipboardEvent,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';

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
  type Editing,
  type FinishEditOptions,
  type Rules,
  type State,
  type Tag as TagState,
  type Validate,
} from './tag_input_state';

export { gettext };

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

type TagInputTextarea = HTMLTextAreaElement & { tagInput?: TagInput };

const MODIFIER_KEYS = ['Shift', 'Control', 'Meta', 'Alt'];

export default class TagInput {
  readonly #store: TagStore;

  static attach(
    textarea: TagInputTextarea,
    config: TagInputConfig = {},
  ): TagInput {
    textarea.tagInput ??= new TagInput(textarea, config);
    return textarea.tagInput;
  }

  private constructor(textarea: HTMLTextAreaElement, config: TagInputConfig) {
    const separators = config.separators ?? ['Enter'];
    const rules: Rules = {
      normalize: config.normalize ?? ((value) => value),
      validate: config.validate ?? (() => true),
      pattern: separatorPattern(separators),
    };
    this.#store = new TagStore(rules, textarea.value);
    keepTextareaInSync(textarea, this.#store);

    // Read before `hideTextarea` turns it off.
    const required = textarea.required;
    const inputId = hideTextarea(textarea);
    // Saves any typed or half-edited entry before the form reads the textarea.
    textarea.form?.addEventListener(
      'submit',
      () => this.#store.update(finishAll),
      { capture: true },
    );

    const mount = document.createElement('div');
    textarea.after(mount);
    // Rendered synchronously so the field is in the page as soon as `attach` returns.
    flushSync(() =>
      createRoot(mount).render(
        <TagInputField
          store={this.#store}
          textarea={textarea}
          inputId={inputId}
          separators={separators}
          required={required}
          invalidMessage={
            config.invalidMessage ??
            gettext('Fix or remove the entries marked in red.')
          }
        />,
      ),
    );
  }

  get valid(): boolean {
    return isValid(this.#store.getState());
  }

  get values(): string[] {
    return values(this.#store.getState());
  }

  removeTag(value: string): void {
    this.#store.update((state) => removeTag(state, value));
  }

  clear(): void {
    this.#store.update(clear);
  }

  editTag(value: string): void {
    this.#store.update((state, rules) => editTag(state, rules, value));
  }
}

type Change = (state: State, rules: Rules) => State;

/** Holds the state. React subscribes to it with `useSyncExternalStore`. */
class TagStore {
  #state: State;
  readonly #rules: Rules;
  readonly #listeners = new Set<() => void>();

  constructor(rules: Rules, storedValue: string) {
    this.#rules = rules;
    this.#state = initialState(rules, storedValue);
  }

  getState = (): State => this.#state;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  update = (change: Change): void => {
    const next = change(this.#state, this.#rules);
    if (next === this.#state) return;
    this.#state = next;
    this.#listeners.forEach((listener) => listener());
  };
}

/** Writes the entries to the textarea and fires `tag-input:change`, now and after each change. */
function keepTextareaInSync(textarea: HTMLTextAreaElement, store: TagStore) {
  const sync = (state: State) => {
    textarea.value = storedValue(state);
    textarea.dispatchEvent(
      new CustomEvent<TagInputChangeDetail>('tag-input:change', {
        bubbles: true,
        detail: { valid: isValid(state), values: values(state) },
      }),
    );
  };

  let previous = store.getState();
  sync(previous);
  store.subscribe(() => {
    const state = store.getState();
    if (state.tags !== previous.tags || state.text !== previous.text) {
      sync(state);
    }
    previous = state;
  });
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

// Components.

interface TagInputFieldProps {
  store: TagStore;
  textarea: HTMLTextAreaElement;
  inputId: string | undefined;
  separators: string[];
  required: boolean;
  invalidMessage: string;
}

function TagInputField({
  store,
  textarea,
  inputId,
  separators,
  required,
  invalidMessage,
}: TagInputFieldProps) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const { update } = store;
  const valid = isValid(state);
  const listRef = useRef<HTMLUListElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const focusInput = () => inputRef.current?.focus();

  useLayoutEffect(() => {
    inputRef.current?.setCustomValidity(
      validityMessage(state, required, invalidMessage),
    );
  }, [state, required, invalidMessage]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    const withModifier = event.ctrlKey || event.metaKey;

    if (withModifier && event.key.toLowerCase() === 'a') {
      if (canSelectAllTags(event.currentTarget, state)) {
        event.preventDefault();
        update((s) => setAllSelected(s, true));
      }
      return;
    }

    if (state.allSelected) {
      if (event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault();
        // Select-all also covers the typed text: it is either empty or was selected too.
        update(clear);
        return;
      }
      if (!withModifier && !MODIFIER_KEYS.includes(event.key)) {
        update((s) => setAllSelected(s, false));
      }
    }

    if (separators.includes(event.key)) {
      event.preventDefault();
      update(commitText);
      return;
    }

    const lastTag = state.tags.at(-1);
    if (event.key === 'Backspace' && state.text === '' && lastTag) {
      // Without this, the default action deletes the last character of the tag's text.
      event.preventDefault();
      update((s, rules) => editTag(s, rules, lastTag.value));
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    const text = withPasted(event);
    update((s, rules) => paste(s, rules, text));
  };

  const copySelectedTags = (event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    event.clipboardData.setData('text/plain', values(state).join('\n'));
  };

  const onCopy = (event: ClipboardEvent<HTMLInputElement>) => {
    if (state.allSelected) copySelectedTags(event);
  };

  const onCut = (event: ClipboardEvent<HTMLInputElement>) => {
    if (!state.allSelected) return;
    copySelectedTags(event);
    update(clear);
  };

  // Clicks on the field's empty space focus the input.
  const onContainerClick = (event: MouseEvent<HTMLDivElement>) => {
    if (
      event.target === event.currentTarget ||
      event.target === listRef.current
    ) {
      focusInput();
    }
  };

  const containerClass = [
    'tag-input',
    !valid && 'tag-input-has-errors',
    state.allSelected && 'tag-input-all-selected',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <>
      <div
        className={containerClass}
        onMouseDown={() => update((s) => setAllSelected(s, false))}
        onClick={onContainerClick}
      >
        <ul ref={listRef} className="tag-input-list">
          {state.tags.map((tag, index) =>
            state.editing?.index === index ? (
              <EditingTag
                key={tag.value}
                editing={state.editing}
                separators={separators}
                onChange={(text) => update((s) => setEditText(s, text))}
                onFinish={(options) =>
                  update((s, rules) => finishEdit(s, rules, options))
                }
                focusInput={focusInput}
              />
            ) : (
              <Tag
                key={tag.value}
                tag={tag}
                onEdit={() =>
                  update((s, rules) => editTag(s, rules, tag.value))
                }
                onRemove={() => {
                  update((s) => removeTag(s, tag.value));
                  focusInput();
                }}
              />
            ),
          )}
        </ul>
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          className="tag-input-field"
          placeholder={textarea.placeholder}
          aria-describedby={
            textarea.getAttribute('aria-describedby') ?? undefined
          }
          value={state.text}
          onChange={(event) =>
            update((s, rules) => setText(s, rules, event.target.value))
          }
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onCopy={onCopy}
          onCut={onCut}
          onBlur={() =>
            update((s, rules) => setAllSelected(commitText(s, rules), false))
          }
        />
      </div>
      <p className="tag-input-error" hidden={valid}>
        {invalidMessage}
      </p>
    </>
  );
}

interface TagProps {
  tag: TagState;
  onEdit: () => void;
  onRemove: () => void;
}

function Tag({ tag: { value, error }, onEdit, onRemove }: TagProps) {
  const className = error
    ? 'tag-input-tag tag-input-tag-invalid'
    : 'tag-input-tag';
  return (
    <li className={className} title={error ?? undefined}>
      <button
        type="button"
        className="tag-input-text"
        aria-label={interpolate(gettext('Edit %s'), [value])}
        onClick={onEdit}
      >
        {value}
      </button>
      {error && <span className="sr-only">({error})</span>}
      <button
        type="button"
        className="tag-input-remove"
        aria-label={interpolate(gettext('Remove %s'), [value])}
        onClick={onRemove}
      >
        <i className="fa-solid fa-xmark" aria-hidden="true" />
      </button>
    </li>
  );
}

interface EditingTagProps {
  editing: Editing;
  separators: string[];
  onChange: (text: string) => void;
  onFinish: (options?: FinishEditOptions) => void;
  focusInput: () => void;
}

function EditingTag({
  editing,
  separators,
  onChange,
  onFinish,
  focusInput,
}: EditingTagProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }, []);

  const finishAndLeave = (options?: FinishEditOptions) => {
    onFinish(options);
    // Moving focus blurs this input, whose blur handler then finds nothing left to finish.
    focusInput();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      // Escape only cancels this edit; it must not also close a modal the field sits in.
      event.stopPropagation();
      finishAndLeave({ cancel: true });
    } else if (event.key === 'Enter' || separators.includes(event.key)) {
      event.preventDefault();
      finishAndLeave();
    }
  };

  // A text input drops pasted line breaks, which would join the lines into one entry, so a
  // paste with line breaks is saved straight away as separate tags.
  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    if (!/[\r\n]/.test(event.clipboardData.getData('text'))) return;
    event.preventDefault();
    finishAndLeave({ text: withPasted(event) });
  };

  return (
    <li className="tag-input-tag tag-input-tag-editing">
      <input
        ref={inputRef}
        type="text"
        className="tag-input-edit"
        size={Math.max(editing.text.length, 1) + 1}
        aria-label={interpolate(gettext('Edit %s'), [editing.original])}
        value={editing.text}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={() => onFinish()}
      />
    </li>
  );
}

// DOM helpers.

function validityMessage(
  state: State,
  required: boolean,
  invalidMessage: string,
): string {
  if (!isValid(state)) return invalidMessage;
  if (required && !storedValue(state)) {
    return gettext('This field is required.');
  }
  return '';
}

/** Ctrl/Cmd+A selects the tags once the input's text is empty or already all selected. */
function canSelectAllTags(input: HTMLInputElement, state: State): boolean {
  const { value, selectionStart, selectionEnd } = input;
  const textAllSelected = selectionStart === 0 && selectionEnd === value.length;
  return state.tags.length > 0 && (value === '' || textAllSelected);
}

/** The input's text with the pasted text in place of its selection. */
function withPasted(event: ClipboardEvent<HTMLInputElement>): string {
  const { value, selectionStart, selectionEnd } = event.currentTarget;
  const pasted = event.clipboardData.getData('text');
  return (
    value.slice(0, selectionStart ?? value.length) +
    pasted +
    value.slice(selectionEnd ?? value.length)
  );
}
