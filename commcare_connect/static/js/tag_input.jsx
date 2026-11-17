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
 * The field is a React component. Every change goes through a pure function from one state
 * (`{tags, text, editing, allSelected}`) to the next, so several changes in one event compose.
 */
import {
  createRef,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';

export const gettext = (text) => (window.gettext ? window.gettext(text) : text);
const MODIFIER_KEYS = ['Shift', 'Control', 'Meta', 'Alt'];
const interpolate = (format, args) =>
  window.interpolate
    ? window.interpolate(format, args)
    : // A function, so "$&" and similar in a tag's value are not read as replacement patterns.
      args.reduce((text, arg) => text.replace('%s', () => arg), format);

export default class TagInput {
  static attach(textarea, config = {}) {
    if (!textarea.tagInput) {
      textarea.tagInput = new TagInput(textarea, config);
    }
    return textarea.tagInput;
  }

  constructor(textarea, config) {
    this._field = createRef();
    const mount = document.createElement('div');
    textarea.after(mount);
    // Rendered synchronously so `valid` and `values` are right as soon as `attach` returns.
    flushSync(() =>
      createRoot(mount).render(
        <TagInputField ref={this._field} textarea={textarea} config={config} />,
      ),
    );
  }

  get valid() {
    return isValid(this._field.current.state);
  }

  get values() {
    return values(this._field.current.state);
  }

  removeTag(value) {
    this._field.current.apply((state) => removeTag(state, value));
  }

  clear() {
    this._field.current.apply(clear);
  }

  editTag(value) {
    this._field.current.apply((state) => editTag(state, value));
  }
}

function TagInputField({ ref, textarea, config }) {
  const separators = config.separators ?? ['Enter'];
  const invalidMessage =
    config.invalidMessage ??
    gettext('Fix or remove the entries marked in red.');
  const rules = {
    normalize: config.normalize ?? ((value) => value),
    validate: config.validate ?? (() => true),
    pattern: separatorPattern(separators),
  };
  const inputId = textarea.id ? `${textarea.id}_tag_input` : undefined;

  const [required] = useState(textarea.required);
  const [state, setState] = useState(() =>
    addEntries(
      { tags: [], text: '', editing: null, allSelected: false, rules },
      parse(textarea.value, /\r?\n/),
    ),
  );
  const inputRef = useRef(null);
  // Changes are queued as functions of the latest state, so two in one event both apply.
  const update = (change) =>
    setState((current) => change({ ...current, rules }));
  // For callers outside React's event handlers, which need the change applied on return.
  const apply = (change) => flushSync(() => update(change));
  const focusInput = () => inputRef.current.focus();

  useImperativeHandle(ref, () => ({ state, apply }));
  useTextareaBinding({ textarea, inputId, apply });
  useLayoutEffect(() => {
    const value = storedValue(state);
    const valid = isValid(state);
    textarea.value = value;
    inputRef.current.setCustomValidity(
      !valid
        ? invalidMessage
        : required && !value
          ? gettext('This field is required.')
          : '',
    );
    textarea.dispatchEvent(
      new CustomEvent('tag-input:change', {
        bubbles: true,
        detail: { valid, values: values(state) },
      }),
    );
  }, [state.tags, state.text]);

  const onKeyDown = (event) => {
    if (event.nativeEvent.isComposing) return;
    const withModifier = event.ctrlKey || event.metaKey;
    if (withModifier && event.key.toLowerCase() === 'a') {
      const { value, selectionStart, selectionEnd } = event.target;
      const textSelected =
        selectionStart === 0 && selectionEnd === value.length;
      if ((value && !textSelected) || state.tags.length === 0) return;
      event.preventDefault();
      update((s) => ({ ...s, allSelected: true }));
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
        update((s) => ({ ...s, allSelected: false }));
      }
    }
    if (separators.includes(event.key)) {
      event.preventDefault();
      update(commitText);
    } else if (event.key === 'Backspace' && state.text === '') {
      const last = state.tags.at(-1);
      if (last) {
        // Without this, the default action deletes the last character of the tag's text.
        event.preventDefault();
        update((s) => editTag(s, last.value));
      }
    }
  };

  const onPaste = (event) => {
    event.preventDefault();
    const text = withPasted(event);
    // Like pasting over selected text, a paste replaces the tags selected with Ctrl/Cmd+A.
    update((s) => {
      const base = s.allSelected ? clear(s) : s;
      return { ...addEntries(base, parse(text, rules.pattern)), text: '' };
    });
  };

  // Mobile keyboards and autofill can insert a separator without a keydown for it.
  const onChange = (event) => {
    const parts = event.target.value.split(rules.pattern);
    const text = parts.pop();
    update((s) => ({ ...addEntries(s, parse(parts.join('\n'), /\n/)), text }));
  };

  const onCopy = (event) => {
    if (!state.allSelected) return false;
    event.preventDefault();
    event.clipboardData.setData('text/plain', values(state).join('\n'));
    return true;
  };

  const containerClass = [
    'tag-input',
    !isValid(state) && 'tag-input-has-errors',
    state.allSelected && 'tag-input-all-selected',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <>
      <div
        className={containerClass}
        onMouseDown={() => update((s) => ({ ...s, allSelected: false }))}
        onClick={(event) => {
          if (
            event.target === event.currentTarget ||
            event.target.tagName === 'UL'
          ) {
            focusInput();
          }
        }}
      >
        <ul className="tag-input-list">
          {state.tags.map((tag, index) =>
            state.editing?.index === index ? (
              <EditingTag
                key={tag.value}
                editing={state.editing}
                separators={separators}
                update={update}
                focusInput={focusInput}
              />
            ) : (
              <Tag
                key={tag.value}
                tag={tag}
                onEdit={() => update((s) => editTag(s, tag.value))}
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
          onChange={onChange}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onCopy={onCopy}
          onCut={(event) => onCopy(event) && update(clear)}
          onBlur={() =>
            update((s) => ({ ...commitText(s), allSelected: false }))
          }
        />
      </div>
      <p className="tag-input-error" hidden={isValid(state)}>
        {invalidMessage}
      </p>
    </>
  );
}

function Tag({ tag, onEdit, onRemove }) {
  const { value, error } = tag;
  return (
    <li
      className={
        error ? 'tag-input-tag tag-input-tag-invalid' : 'tag-input-tag'
      }
      title={error ?? undefined}
    >
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

function EditingTag({ editing, separators, update, focusInput }) {
  const inputRef = useRef(null);
  useLayoutEffect(() => {
    const input = inputRef.current;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }, []);

  const finish = (options) => {
    update((s) => finishEdit(s, options));
    // Moving focus blurs this input, whose blur handler then finds nothing left to finish.
    focusInput();
  };

  const onKeyDown = (event) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      // Escape only cancels this edit; it must not also close a modal the field sits in.
      event.stopPropagation();
      finish({ cancel: true });
    } else if (event.key === 'Enter' || separators.includes(event.key)) {
      event.preventDefault();
      finish();
    }
  };

  // A text input drops pasted line breaks, which would join the lines into one entry, so a
  // paste with line breaks is saved straight away as separate tags.
  const onPaste = (event) => {
    if (!/[\r\n]/.test(event.clipboardData.getData('text'))) return;
    event.preventDefault();
    finish({ text: withPasted(event) });
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
        onChange={(event) =>
          update((s) => ({
            ...s,
            editing: { ...s.editing, text: event.target.value },
          }))
        }
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={() => update((s) => finishEdit(s))}
      />
    </li>
  );
}

// Points the field's <label for> at the visible input, hides the textarea, and saves any typed
// or half-edited entry before the form submits.
function useTextareaBinding({ textarea, inputId, apply }) {
  const applyRef = useRef(apply);
  applyRef.current = apply;

  useLayoutEffect(() => {
    if (inputId) {
      document
        .querySelectorAll(`label[for="${textarea.id}"]`)
        .forEach((label) => (label.htmlFor = inputId));
    }
    textarea.hidden = true;
    // A hidden control can't show its own validation message; the visible input reports it instead.
    textarea.required = false;

    const onSubmit = () => applyRef.current((s) => commitText(finishEdit(s)));
    textarea.form?.addEventListener('submit', onSubmit, { capture: true });
    return () =>
      textarea.form?.removeEventListener('submit', onSubmit, { capture: true });
  }, []);
}

// State changes. Each takes the current state and returns the next one.

function addEntries(state, entries) {
  const existing = new Set(values(state));
  const tags = [...state.tags];
  entries.forEach((entry) => {
    const tag = createTag(entry, existing, state.rules);
    if (!tag) return;
    tags.push(tag);
    existing.add(tag.value);
  });
  return { ...state, tags };
}

function commitText(state) {
  return { ...addEntries(state, [clean(state.text)]), text: '' };
}

function removeTag(state, value) {
  const index = state.tags.findIndex((tag) => tag.value === value);
  if (index < 0) return state;
  return {
    ...state,
    tags: state.tags.filter((tag) => tag.value !== value),
    editing: state.editing?.index === index ? null : state.editing,
  };
}

function clear(state) {
  return { ...state, tags: [], text: '', editing: null, allSelected: false };
}

function editTag(state, value) {
  if (state.editing?.original === value) return state;
  const next = commitText(finishEdit(state));
  const index = next.tags.findIndex((tag) => tag.value === value);
  if (index < 0) return next;
  return { ...next, editing: { index, original: value, text: value } };
}

// Saves the tag being edited, in its place: no text removes it, a duplicate of another tag is
// dropped, and text with separators in it becomes several tags.
function finishEdit(state, { cancel = false, text = null } = {}) {
  if (!state.editing) return state;
  const { index, original } = state.editing;
  const before = { ...state, tags: state.tags.slice(0, index) };
  const others = new Set(
    state.tags.filter((_, i) => i !== index).map((tag) => tag.value),
  );
  const entries = cancel
    ? [original]
    : parse(text ?? state.editing.text, state.rules.pattern);
  const replacements = [];
  entries.forEach((entry) => {
    const tag = createTag(entry, others, state.rules);
    if (!tag) return;
    replacements.push(tag);
    others.add(tag.value);
  });
  return {
    ...before,
    tags: [...before.tags, ...replacements, ...state.tags.slice(index + 1)],
    editing: null,
  };
}

// Reading state.

function values(state) {
  return state.tags.map((tag) => tag.value);
}

function isValid(state) {
  return state.tags.every((tag) => tag.error === null);
}

function storedValue(state) {
  const pending = clean(state.text);
  return [...values(state), ...(pending ? [pending] : [])].join('\n');
}

// Text helpers.

// A tag for the entry, or null when it is empty or already in `existingValues`.
function createTag(entry, existingValues, { normalize, validate }) {
  const value = clean(normalize(entry));
  if (!value || existingValues.has(value)) return null;
  const result = validate(value);
  const error =
    result === true
      ? null
      : typeof result === 'string'
        ? result
        : gettext('Invalid entry');
  return { value, error };
}

// The input's text with the pasted text in place of its selection.
function withPasted(event) {
  const { value, selectionStart, selectionEnd } = event.target;
  const pasted = event.clipboardData.getData('text');
  return value.slice(0, selectionStart) + pasted + value.slice(selectionEnd);
}

function parse(text, pattern) {
  return text
    .split(pattern)
    .map((part) => clean(part))
    .filter(Boolean);
}

function clean(text) {
  return text.replace(/\s+/g, ' ').trim();
}

// A line break always separates entries: an entry can't hold one, and without this a pasted
// line break would be collapsed into a space and joined onto the next entry.
function separatorPattern(separators) {
  const parts = separators
    .filter((separator) => separator !== 'Enter')
    .map((separator) =>
      separator === ' '
        ? '\\s'
        : separator.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
    );
  return new RegExp([...parts, '\\r?\\n'].join('|'));
}
