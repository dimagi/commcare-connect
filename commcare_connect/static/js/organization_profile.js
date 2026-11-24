// Email and EOI link tags for the organization profile forms: the create wizard, Organization Home
// and the directory's add/edit modal.
import { gettext, interpolate } from './i18n';
import TagInput from './tag_input';

// Loose on purpose: the server's EmailValidator has the final say.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// The schemes Django's URLValidator accepts.
const URL_PROTOCOLS = ['http:', 'https:', 'ftp:', 'ftps:'];
const URL_PREFIXES = URL_PROTOCOLS.map((protocol) => `${protocol}//`).join(
  ', ',
);

function validateEmail(value) {
  return EMAIL.test(value) || gettext('Enter a valid email address.');
}

function validateUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    url = null;
  }
  return (
    (url !== null && URL_PROTOCOLS.includes(url.protocol)) ||
    interpolate(gettext('Enter a valid URL, starting with one of: %s'), [
      URL_PREFIXES,
    ])
  );
}

function initOrganizationProfileFields() {
  document
    .querySelectorAll('textarea[name="contact_emails"]')
    .forEach((textarea) =>
      TagInput.attach(textarea, {
        separators: [',', ' ', 'Enter'],
        validate: validateEmail,
      }),
    );
  document.querySelectorAll('textarea[name="eoi_links"]').forEach((textarea) =>
    TagInput.attach(textarea, {
      separators: [' ', 'Enter'],
      validate: validateUrl,
    }),
  );
}

document.addEventListener('DOMContentLoaded', initOrganizationProfileFields);

// The directory's add/edit modal arrives through htmx. Waiting for the settle step matters: htmx
// restores the server-rendered attributes of a same-id element during settle, which would undo
// the textarea's `hidden` if this ran at insertion time.
document.addEventListener('htmx:afterSettle', initOrganizationProfileFields);
