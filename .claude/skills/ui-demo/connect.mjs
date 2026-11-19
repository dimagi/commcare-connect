// connect.mjs — commcare-connect helpers for ui-demo flows. Copy next to flow.mjs with runner.mjs.
// Every helper takes (page, d) so it is plain Playwright in the Verify Run and polished under --record.
// Selectors come from the commcare-connect templates — re-verify with a Verify Run after template changes.
import { expect } from './runner.mjs';

export const PORT = process.env.DEMO_PORT || '8010';
export const BASE = `http://localhost:${PORT}`;

// Matches seed.py. Names/emails encode the role so the viewer can tell who is who on screen.
export const ORG = {
  name: 'Child Health Campaign',
  slug: 'child-health-campaign',
}; // slug is derived from the name by the model
export const OTHER_ORGS = ['Kangaroo Mother Care', 'Mother Baby Wellness']; // seeded as extra workspaces (slugs derived)
export const PASSWORD = 'Connect2026!';
const DOMAIN = 'childhealth.org';
export const USERS = {
  admin: {
    email: `asha.admin@${DOMAIN}`,
    name: 'Asha Admin',
    role: 'admin',
    staff: true,
  },
  admin2: { email: `ravi.admin@${DOMAIN}`, name: 'Ravi Admin', role: 'admin' },
  member: {
    email: `meera.member@${DOMAIN}`,
    name: 'Meera Member',
    role: 'member',
  },
  viewer: {
    email: `vikram.viewer@${DOMAIN}`,
    name: 'Vikram Viewer',
    role: 'viewer',
  },
  existing: {
    email: `isha.invitee@${DOMAIN}`,
    name: 'Isha Invitee',
    role: null,
  }, // has a Connect account, not in the org
  newcomer: { email: `neel.new@${DOMAIN}`, name: 'Neel New', role: null }, // no account yet (deleted on reseed)
};

export const urls = {
  login: `${BASE}/accounts/login/`,
  orgHome: `${BASE}/a/${ORG.slug}/organization/`,
  opportunities: `${BASE}/a/${ORG.slug}/opportunity/`,
  admin: (path = '') => `${BASE}/admin/${path}`,
};

// ---------- auth
/** Off-camera sign-in for run({ preAuth }) — plain Playwright, no cursor/overlays. Use when login is NOT the feature. */
export async function quickLogin(page, user, password = PASSWORD) {
  await page.goto(urls.login);
  await page.fill('input[name="login"]', user.email);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.includes('/accounts/login'));
}

/** On-camera sign-in — only when signing in is part of the story (auth, invites, permissions). */
export async function login(page, d, user, password = PASSWORD) {
  await d.goto(page, urls.login);
  await fillLogin(page, d, user, password);
}

/** Fill the login form already on screen (e.g. after a redirect to login — keeps ?next= intact). */
export async function fillLogin(page, d, user, password = PASSWORD) {
  await d.type(page, 'input[name="login"]', user.email);
  await d.type(page, 'input[name="password"]', password);
  await d.click(page, 'button[type="submit"]');
  await page.waitForLoadState('networkidle').catch(() => {});
}

/** Switch identity mid-flow (clears cookies first). Set `useState: false` in run() when you use this. */
export async function switchUser(page, d, user, password = PASSWORD) {
  await page.context().clearCookies();
  await login(page, d, user, password);
}

export async function assertLoggedIn(page) {
  await expect(page).not.toHaveURL(/accounts\/login/);
  await expect(page.locator('#djDebug')).toHaveCount(0); // debug toolbar must be off (record_settings.py)
}

// ---------- organization
export async function openOrgMembers(page, d) {
  await d.goto(page, urls.orgHome);
  await d.click(page, 'li:has-text("Members")');
  await expect(page.locator('#invoice_members_container')).toBeVisible();
}
export const sel = {
  membersTable: '#invoice_members_container',
  pendingInvites: '#pending_invites_container',
  addMembers: 'button:has-text("Add Members")',
  inviteModal: '.modal:has-text("Invite Member")',
};

export async function inviteMember(page, d, email, role = 'Member') {
  await d.click(page, sel.addMembers);
  const modal = page.locator(sel.inviteModal);
  await modal.waitFor({ state: 'visible' });
  await d.type(page, modal.locator('input[name="email"]'), email);
  await d.select(page, modal.locator('select[name="role"]'), { label: role });
  await d.click(
    page,
    modal.locator('input[type="submit"], button[type="submit"]').first(),
  );
  await page.waitForLoadState('networkidle').catch(() => {});
}

// ---------- TomSelect (countries, primary sectors, year of establishment, work areas)
/** Picks a TomSelect value by typing to filter, the way a user would. `option` is the text to type, or
 *  `{ text, value }` when the option's value is not its text (a multi-select keyed by pk). Every line is
 *  load-bearing: reference/troubleshooting.md "TomSelect picker". */
export async function pickTomSelect(page, d, id, option) {
  const { text, value = text } =
    typeof option === 'string' ? { text: option } : option;
  // 1. the visible control, not the size="1" input: the wrapper intercepts real-coordinate clicks under --record.
  //    Skipped while TomSelect has focus (the next pick on a multi-select): the search box is still live, and the
  //    click would land on a chosen chip
  const focused = await page.evaluate(
    (sel) => document.querySelector(sel).tomselect.isFocused,
    `#${id}`,
  );
  if (!focused)
    await d.click(page, `.ts-wrapper:has(#${id}-ts-control) .ts-control`);
  // 2. TomSelect drops keystrokes until it has registered focus itself, and while a chip is active (a click on the
  //    control lands on one once something is picked); a query left by the previous pick would join the new one
  await page.evaluate((sel) => {
    const ts = document.querySelector(sel).tomselect;
    ts.setActiveItem(); // no argument: deactivates every chip and shows the search box again
    ts.setTextboxValue('');
    ts.focus();
  }, `#${id}`);
  await page
    .locator(`#${id}-ts-control`)
    .pressSequentially(text, { delay: 60 });
  // 3. prove the search filtered, so a dropped keystroke fails loudly instead of clicking an unfiltered row
  await expect(
    page.locator(`.ts-wrapper:has(#${id}-ts-control) .ts-dropdown .option`),
  ).toHaveCount(1);
  // 4. by data-value, not text: once filtered the match is wrapped in <span class="highlight"> and :text-is() misses it
  await d.click(
    page,
    `.ts-wrapper:has(#${id}-ts-control) .ts-dropdown .option[data-value="${value}"]`,
  );
  // 5. a multi-select keeps its dropdown open after a pick, over the fields below it, and the record cursor's next
  //    click lands on an option instead. Closing keeps focus, so the next pick still types straight in
  await page.evaluate((sel) => {
    const ts = document.querySelector(sel).tomselect;
    if (ts.settings.mode === 'multi') ts.close();
  }, `#${id}`);
}

// ---------- django admin
export async function openAdmin(page, d, path) {
  await d.goto(page, urls.admin(path));
  await expect(page.locator('#content')).toBeVisible();
}
