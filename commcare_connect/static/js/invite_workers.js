// Phone number tags for the "users" field on Invite Connect Workers and Edit Opportunity.
// The Submit button (id "send-invites", invite page only) is disabled while any tag is invalid.
import { gettext } from './i18n';
import TagInput from './tag_input';

// '+' then 7 to 14 digits. The upper bound fits the phone_number column (max_length=15,
// including the '+'). The lower bound catches a country code on its own, such as "+27".
const PHONE_NUMBER = /^\+\d{7,14}$/;

// People write numbers as "+27 82 123-4567" or "+1 (555) 123 4567"; the server accepts digits only.
function normalizePhoneNumber(value) {
  return value.replace(/[\s().-]/g, '');
}

function validatePhoneNumber(value) {
  return (
    PHONE_NUMBER.test(value) ||
    gettext(
      "Phone numbers must be '+' followed by the country code and number, 7 to 14 digits in all.",
    )
  );
}

function initInviteWorkers() {
  const textarea = document.querySelector('textarea[name="users"]');
  if (!textarea) return;

  const tagInput = TagInput.attach(textarea, {
    // No space: it would split "+27 82 123 4567" into several tags.
    separators: ['Enter', ','],
    normalize: normalizePhoneNumber,
    validate: validatePhoneNumber,
    invalidMessage: gettext(
      'Fix or remove the phone numbers marked in red before sending invites.',
    ),
  });

  const sendButton = document.getElementById('send-invites');
  if (sendButton) {
    sendButton.disabled = !tagInput.valid;
    textarea.addEventListener('tag-input:change', (event) => {
      sendButton.disabled = !event.detail.valid;
    });
  }
}

document.addEventListener('DOMContentLoaded', initInviteWorkers);
