# Host tree, view access and access requests (3.0.0-alpha.6)

## Host tree

Admin → Host Tree. Host groups can be nested (up to 8 levels). A rule or viewer on a group also
covers the hosts of every group below it. A group that still has child groups cannot be deleted;
move or delete the children first.

## Viewers: see, not log in

Add users or user groups as **viewers** of a group in the Host Tree. Viewers see the hosts under
that group (Hosts, Posture) but cannot open a terminal, use SFTP or reveal a credential. Logging
in still needs an access rule with an account.

## Requesting access

On Hosts, a user who can see a host but not log in to it clicks **Request access**, picks the
account, a duration (1 hour, 8 hours, 1 day or 7 days) and gives a reason. The request appears
in Approvals → Access rules for the admins. The time starts when an admin approves it, and the
access ends by itself when it runs out. The user sees their requests under "My access requests"
and can cancel a pending one. Nobody can approve a request for themselves.

Admin-created access rules are active at once while `SEYALRUN_ADMIN_APPROVAL=off` (the
default); see [PATCHING.md](PATCHING.md#second-admin-approval-switch).

## Notes

- SFTP now refuses a session when no access rule matches it at all (before, it was treated as
  unrestricted).
- Open terminal sessions are not cut when a request expires; new logins stop.
- Command filters and rollout group targets still use a group's own hosts only, not its children.
