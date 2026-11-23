// flow.mjs — example for a feature that is NOT about auth: start signed in, on the page where the feature lives.
// (Compare flow.example.mjs, where signing in is part of the story and stays on camera.)
import { run, expect } from './runner.mjs';
import * as c from './connect.mjs';

const INTRO = {
  title: 'Take back an invite sent by mistake',
  description: 'Until it is accepted, an admin can withdraw it in one click',
};
const OUTRO = {
  title: 'The invite is withdrawn',
  description: 'The link no longer works, so Isha cannot join',
};

const STEPS = [
  {
    name: 'members',
    run: (p, d) => c.openOrgMembers(p, d),
    assert: (p) =>
      expect(p.locator(c.sel.membersTable)).toContainText(c.USERS.member.email),
    intent: 'Open the workspace members',
    caption: ['Workspace members', 'Asha is already signed in as admin'],
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
    caption: ['An invite is pending', 'Isha has not accepted yet'],
  },
  {
    name: 'revoke',
    run: async (p, d) => {
      const row = p.locator(
        `${c.sel.pendingInvites} tr:has-text("${c.USERS.existing.email}")`,
      );
      await d.click(
        p,
        row.locator('a:has-text("Revoke"), button:has-text("Revoke")').first(),
      );
    },
    assert: (p) =>
      expect(p.locator(c.sel.pendingInvites)).not.toContainText(
        c.USERS.existing.email,
      ),
    intent: "Revoke Isha's invite",
    caption: ['Invite revoked', 'Removed from Pending Invites immediately'],
    checkpoint: c.sel.pendingInvites,
  },
];

await run(
  {
    BASE: c.BASE,
    INTRO,
    OUTRO,
    STEPS,
    preAuth: (p) => c.quickLogin(p, c.USERS.admin),
    firstUrl: c.urls.orgHome,
  },
  import.meta.url,
);
