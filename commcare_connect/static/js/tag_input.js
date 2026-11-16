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
 */
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
    this.textarea = textarea;
    this.separators = config.separators ?? ['Enter'];
    this.normalize = config.normalize ?? ((value) => value);
    this.validate = config.validate ?? (() => true);
    this.invalidMessage =
      config.invalidMessage ??
      gettext('Fix or remove the entries marked in red.');
    this.required = textarea.required;
    this.tags = [];
    this.allSelected = false;
    // The tag being edited in place and the input that replaced its text, or null.
    this.editing = null;

    this._render();
    this._bindEvents();
    this._parse(textarea.value, /\r?\n/).forEach((value) =>
      this._addTag(value),
    );
    this._sync();
  }

  get valid() {
    return this.tags.every((tag) => tag.error === null);
  }

  get values() {
    return this.tags.map((tag) => tag.value);
  }

  removeTag(value) {
    const tag = this._findTag(value);
    if (!tag) return;
    if (this.editing?.tag === tag) this.editing = null;
    tag.element.remove();
    this.tags = this.tags.filter((t) => t !== tag);
    this._sync();
  }

  clear() {
    this.editing = null;
    this.tags.forEach((tag) => tag.element.remove());
    this.tags = [];
    this._setAllSelected(false);
    this._sync();
  }

  editTag(value) {
    const tag = this._findTag(value);
    if (!tag || this.editing?.tag === tag) return;
    this._finishEdit();
    this._commitInput();

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'tag-input-edit';
    input.value = value;
    input.setAttribute('aria-label', interpolate(gettext('Edit %s'), [value]));
    const fitToText = () => (input.size = Math.max(input.value.length, 1) + 1);
    fitToText();
    input.addEventListener('input', fitToText);
    input.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        // Escape only cancels this edit; it must not also close a modal the field sits in.
        event.stopPropagation();
        this._finishEdit({ cancel: true });
      } else if (event.key === 'Enter' || this.separators.includes(event.key)) {
        event.preventDefault();
        this._finishEdit();
      }
    });
    input.addEventListener('blur', () =>
      this._finishEdit({ focusInput: false }),
    );
    // A text input drops pasted line breaks, which would join the lines into one entry, so a
    // paste with line breaks is saved straight away as separate tags.
    input.addEventListener('paste', (event) => {
      const pasted = event.clipboardData.getData('text');
      if (!/[\r\n]/.test(pasted)) return;
      event.preventDefault();
      const { value, selectionStart, selectionEnd } = input;
      this._finishEdit({
        text:
          value.slice(0, selectionStart) + pasted + value.slice(selectionEnd),
      });
    });

    tag.element.className = 'tag-input-tag tag-input-tag-editing';
    tag.element.replaceChildren(input);
    this.editing = { tag, input };
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  _findTag(value) {
    return this.tags.find((tag) => tag.value === value);
  }

  _render() {
    this.container = document.createElement('div');
    this.container.className = 'tag-input';
    this.list = document.createElement('ul');
    this.list.className = 'tag-input-list';
    this.input = document.createElement('input');
    this.input.type = 'text';
    this.input.className = 'tag-input-field';
    this.input.placeholder = this.textarea.placeholder;
    if (this.textarea.id) {
      // The field's <label for> points at the textarea id; let it focus the visible input instead.
      this.input.id = `${this.textarea.id}_tag_input`;
      document
        .querySelectorAll(`label[for="${this.textarea.id}"]`)
        .forEach((label) => (label.htmlFor = this.input.id));
    }
    const describedBy = this.textarea.getAttribute('aria-describedby');
    if (describedBy) this.input.setAttribute('aria-describedby', describedBy);

    this.error = document.createElement('p');
    this.error.className = 'tag-input-error';
    this.error.textContent = this.invalidMessage;
    this.error.hidden = true;

    this.container.append(this.list, this.input);
    this.textarea.after(this.container, this.error);
    this.textarea.hidden = true;
    // A hidden control can't show its own validation message; the visible input reports it instead.
    this.textarea.required = false;
  }

  _bindEvents() {
    this.container.addEventListener('click', (event) => {
      if (event.target === this.container || event.target === this.list) {
        this.input.focus();
      }
    });
    this.container.addEventListener('mousedown', () =>
      this._setAllSelected(false),
    );
    this.input.addEventListener('keydown', (event) => this._onKeydown(event));
    this.input.addEventListener('paste', (event) => this._onPaste(event));
    this.input.addEventListener('copy', (event) => this._copyAllTags(event));
    this.input.addEventListener('cut', (event) => {
      if (this._copyAllTags(event)) this.clear();
    });
    this.input.addEventListener('input', () => this._onInput());
    this.input.addEventListener('blur', () => {
      this._setAllSelected(false);
      this._commitInput();
    });
    this.textarea.form?.addEventListener(
      'submit',
      () => {
        this._finishEdit({ focusInput: false });
        this._commitInput();
      },
      { capture: true },
    );
  }

  _onKeydown(event) {
    if (event.isComposing) return;
    const withModifier = event.ctrlKey || event.metaKey;
    if (withModifier && event.key.toLowerCase() === 'a') {
      if (this._selectAllTags()) event.preventDefault();
      return;
    }
    if (this.allSelected) {
      if (event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault();
        // Select-all also covers the typed text: it is either empty or was selected too.
        this.input.value = '';
        this.clear();
        return;
      }
      if (!withModifier && !MODIFIER_KEYS.includes(event.key)) {
        this._setAllSelected(false);
      }
    }
    if (this.separators.includes(event.key)) {
      event.preventDefault();
      this._commitInput();
    } else if (event.key === 'Backspace' && this.input.value === '') {
      const last = this.tags.at(-1);
      if (last) {
        // Without this, the default action deletes the last character of the tag's text.
        event.preventDefault();
        this.editTag(last.value);
      }
    }
  }

  // Selects every tag instead of the input's text, once that text is empty or already selected.
  _selectAllTags() {
    const { value, selectionStart, selectionEnd } = this.input;
    const textSelected = selectionStart === 0 && selectionEnd === value.length;
    if ((value && !textSelected) || this.tags.length === 0) return false;
    this._setAllSelected(true);
    return true;
  }

  _setAllSelected(selected) {
    this.allSelected = selected;
    this.container.classList.toggle('tag-input-all-selected', selected);
  }

  _copyAllTags(event) {
    if (!this.allSelected) return false;
    event.preventDefault();
    event.clipboardData.setData('text/plain', this.values.join('\n'));
    return true;
  }

  _onPaste(event) {
    event.preventDefault();
    const pasted = event.clipboardData.getData('text');
    // Like pasting over selected text, a paste replaces the tags selected with Ctrl/Cmd+A.
    if (this.allSelected) this.clear();
    const { value, selectionStart, selectionEnd } = this.input;
    const text =
      value.slice(0, selectionStart) + pasted + value.slice(selectionEnd);
    this._parse(text, this._separatorPattern()).forEach((entry) =>
      this._addTag(entry),
    );
    this.input.value = '';
    this._sync();
  }

  _onInput() {
    // Mobile keyboards and autofill can insert a separator without a keydown for it.
    const parts = this.input.value.split(this._separatorPattern());
    if (parts.length > 1) {
      this.input.value = parts.pop();
      this._parse(parts.join('\n'), /\n/).forEach((value) =>
        this._addTag(value),
      );
    }
    this._sync();
  }

  _commitInput() {
    const value = this._clean(this.input.value);
    this.input.value = '';
    if (value) this._addTag(value);
    this._sync();
  }

  _addTag(entry) {
    const tag = this._createTag(entry, new Set(this.values));
    if (!tag) return;
    this.tags.push(tag);
    this.list.append(tag.element);
  }

  // Saves the tag being edited, in its place: no text removes it, a duplicate of another tag is
  // dropped, and text with separators in it becomes several tags.
  _finishEdit({ cancel = false, focusInput = true, text = null } = {}) {
    if (!this.editing) return;
    const { tag, input } = this.editing;
    // Cleared first: moving focus away blurs the edit input, which would finish it again.
    this.editing = null;

    const index = this.tags.indexOf(tag);
    const others = new Set(
      this.tags.filter((t) => t !== tag).map((t) => t.value),
    );
    const entries = cancel
      ? [tag.value]
      : this._parse(text ?? input.value, this._separatorPattern());
    const newTags = [];
    entries.forEach((entry) => {
      const newTag = this._createTag(entry, others);
      if (!newTag) return;
      newTags.push(newTag);
      others.add(newTag.value);
    });

    tag.element.replaceWith(...newTags.map((t) => t.element));
    this.tags.splice(index, 1, ...newTags);
    if (focusInput) this.input.focus();
    this._sync();
  }

  // A tag for the entry, or null when it is empty or already in `existingValues`.
  _createTag(entry, existingValues) {
    const value = this._clean(this.normalize(entry));
    if (!value || existingValues.has(value)) return null;
    const result = this.validate(value);
    const error =
      result === true
        ? null
        : typeof result === 'string'
          ? result
          : gettext('Invalid entry');
    return { value, error, element: this._renderTag(value, error) };
  }

  _renderTag(value, error) {
    const item = document.createElement('li');
    item.className = error
      ? 'tag-input-tag tag-input-tag-invalid'
      : 'tag-input-tag';
    if (error) item.title = error;

    const text = document.createElement('button');
    text.type = 'button';
    text.className = 'tag-input-text';
    text.textContent = value;
    text.setAttribute('aria-label', interpolate(gettext('Edit %s'), [value]));
    text.addEventListener('click', () => this.editTag(value));
    item.append(text);

    if (error) {
      const description = document.createElement('span');
      description.className = 'sr-only';
      description.textContent = `(${error})`;
      item.append(description);
    }

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'tag-input-remove';
    remove.setAttribute(
      'aria-label',
      interpolate(gettext('Remove %s'), [value]),
    );
    remove.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
    remove.addEventListener('click', () => {
      this.removeTag(value);
      this.input.focus();
    });
    item.append(remove);
    return item;
  }

  _sync() {
    const pending = this._clean(this.input.value);
    this.textarea.value = [...this.values, ...(pending ? [pending] : [])].join(
      '\n',
    );

    const valid = this.valid;
    this.error.hidden = valid;
    this.container.classList.toggle('tag-input-has-errors', !valid);
    if (!valid) {
      this.input.setCustomValidity(this.invalidMessage);
    } else if (this.required && !this.textarea.value) {
      this.input.setCustomValidity(gettext('This field is required.'));
    } else {
      this.input.setCustomValidity('');
    }

    this.textarea.dispatchEvent(
      new CustomEvent('tag-input:change', {
        bubbles: true,
        detail: { valid, values: this.values },
      }),
    );
  }

  _parse(text, pattern) {
    return text
      .split(pattern)
      .map((part) => this._clean(part))
      .filter(Boolean);
  }

  _clean(text) {
    return text.replace(/\s+/g, ' ').trim();
  }

  // A line break always separates entries: an entry can't hold one, and without this a pasted
  // line break would be collapsed into a space and joined onto the next entry.
  _separatorPattern() {
    const parts = this.separators
      .filter((separator) => separator !== 'Enter')
      .map((separator) =>
        separator === ' '
          ? '\\s'
          : separator.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
      );
    return new RegExp([...parts, '\\r?\\n'].join('|'));
  }
}
