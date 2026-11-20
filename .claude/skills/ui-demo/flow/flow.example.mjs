// flow.mjs — example where signing in is part of the story: admin signs in on camera and invites a colleague.
// Everything project-specific comes from connect.mjs; everything recording-specific from runner.mjs.
import { run, expect } from './runner.mjs';
import * as c from './connect.mjs';

const INTRO = {
  title: 'Bring a colleague into a workspace',
  description: 'Invite them with the right role in one step',
};
const OUTRO = {
  title: 'Isha is invited',
  description: 'She joins as a Member once she accepts',
};

const STEPS = [
  {
    name: 'admin-login',
    run: (p, d) => c.login(p, d, c.USERS.admin),
    assert: c.assertLoggedIn,
    intent: 'Asha signs in as admin',
    caption: ['Asha signs in', 'Workspace admin of Child Health Campaign'],
  },
  {
    name: 'members',
    run: (p, d) => c.openOrgMembers(p, d),
    assert: (p) =>
      expect(p.locator(c.sel.membersTable)).toContainText(c.USERS.admin2.email),
    intent: 'Open the workspace members',
    caption: [
      'Two admins run this workspace',
      'Plus a member and a viewer, each with their own access',
    ],
    checkpoint: c.sel.membersTable,
  },
  {
    name: 'invite',
    run: (p, d) => c.inviteMember(p, d, c.USERS.existing.email, 'Member'),
    assert: (p) =>
      expect(p.locator(c.sel.pendingInvites)).toContainText(
        c.USERS.existing.email,
      ),
    intent: 'Invite Isha as a Member',
    caption: [
      'Invite sent to Isha',
      'Listed under Pending Invites until she accepts',
    ],
    checkpoint: c.sel.pendingInvites,
  },
];

await run(
  {
    BASE: c.BASE,
    INTRO,
    OUTRO,
    STEPS,
    firstUrl: c.urls.login,
    useState: false,
  },
  import.meta.url,
);
