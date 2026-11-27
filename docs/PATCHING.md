# Linux patching (3.0 M3 and M4)

Package operations and patch rollouts for Debian/Ubuntu (apt), RHEL family (dnf, yum) and
SUSE (zypper). Windows is not supported. Nothing is fetched from the internet by SeyalRun:
hosts install from the repositories they can reach, normally your internal mirrors.

## How a change reaches a host

`posture-service` decides, `automation-service` connects. posture-service never opens SSH and
never sees a credential. Each operation has a token. The host itself refuses to run the same
token twice, and the work runs detached on the host, so a dropped connection or a restart of
either service resumes the operation instead of repeating it or killing a package transaction.

The host needs: bash, and root or a sudo rule for the credential's user. `flock`, `gzip`, `ss`
and `curl` are used when present. Holding packages on dnf/yum needs the versionlock plugin.
Hosts behind a jump gateway are not supported by these operations yet.

## Package operations on one host

Posture → host → Package operation (admin). `plan` and `refresh` change no package. On a
production host every other operation waits for another person's approval.

Safeguards: every name must be exactly one package (no patterns, no "provides"); the editors of
the mirrors used may not approve the operation; package names are strictly validated; held packages are skipped and reported; an
upgrade that would need to remove a package is refused; a removal that would take a protected
package (kernel, libc, sshd, sudo, the package manager…) with it is refused; a metadata refresh
that failed or could not be verified stops the operation.

## Mirrors

Patching → Mirrors. A mirror has a family, a URL, host groups and an OpenPGP public key. The key
is required. Hosts in the groups then patch only from their mirrors. Removing a mirror removes
its files from the host on the next rollout, also when it was the last one.

Promotion of mirror snapshots (Pulp or similar) is done in the mirror: point the mirror URL at
the promoted distribution. SeyalRun does not drive Pulp.

## Rollouts

1. **Baseline**: what to patch (security updates, everything, the packages behind chosen
   findings, or named packages), waves, checks, reboot policy, Zabbix, drain hooks.
2. **Plan**: exact packages per host and the order of hosts are frozen. Editing the baseline
   later does not change an existing rollout. Hosts install exactly the approved versions, and fail
   when the mirror no longer has one. Set "Versions" to "or newer" in the baseline to accept newer
   ones. Dependencies a package pulls in are chosen by the package manager.
3. **Approve**: another admin, who neither created the rollout nor edited the baseline, window,
   mirror or hook template it uses. The approval is bound to the plan's hash.
   `POSTURE_PATCH_APPROVAL=production` limits this to rollouts that touch a production host.
4. **Run**: waves start only inside the maintenance window. When the window closes, hosts in
   progress finish and no new host starts.

Default waves for 300 hosts: 3 (at least one of each family), 10 %, then 50 at a time, 25 hosts in
parallel, 10 minutes soak after each wave. Two members of a cluster group are never in one wave,
and a host is not taken while a peer is down, failed or drained.

Per host:

| Step | Failure means |
|---|---|
| Mirrors | Host fails, nothing patched |
| Pre-checks: disk, package database, sessions, health URLs, Zabbix problems, cluster peers | Host fails, nothing changed |
| Zabbix maintenance (data collection continues) | Skipped and recorded, or host fails when Zabbix is "required" |
| Drain hook | Host fails |
| Patch | Host fails, stays drained and in maintenance |
| Facts, reboot if needed | Same |
| Post-checks: new boot, services, ports, mounts, health URLs | Same |
| Rescan: every planned package is at the planned version or newer; findings closed | Same. A finding still open is reported, not failed: a feed can list a fix your mirror does not have yet |
| Undrain hook, end maintenance | Host fails |

When more hosts fail than `max_failures` allows (default 0), or a new Zabbix problem appears on a
patched host during the soak, the rollout halts. It resumes only when failed hosts are retried or
skipped. Per failed host:

- **Retry**: back in the queue, so the window, pause and wave limits apply. Checks and rollback
  still cover everything since the first attempt.
- **Rollback**: put the recorded versions back. Only while the rollout is running, paused or halted:
  its approval ends with it. Later, use a package operation, which has its own approval.
- **Release**: undrain and end maintenance after you checked it.
- **Force release**: only when Release cannot work (the undrain hook was edited or deleted, Zabbix
  is gone). It runs nothing. It records that a person undrained the host and ended its maintenance,
  with a reason, and fails if that cannot be written to the audit log.
- **Skip**.

Zabbix maintenance is extended while a host is being worked on or waits for a person.

A reader who may see only some hosts sees only those hosts in a rollout, its counts and its
evidence.

A hook that may already have run when a service restarted is not run again: the host fails and a
person decides. Nobody who ever edited a hook template may approve a rollout that uses it.

Rollback needs the old versions in the mirror and never downgrades a kernel. Drain and undrain
hooks should be safe to repeat; a hook edited after planning is refused.

Evidence (JSON, CSV): packages before and after, CVEs and their state, requester, approver, plan
hash, feed version, job runs and every check.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `POSTURE_PATCH_APPROVAL` | `always` | `always` or `production` |
| `POSTURE_ROLLOUT_TICK_SECONDS` | `5` | How often rollouts advance |
| `POSTURE_STEP_TIMEOUT_MINUTES` | `120` | A step longer than this fails its host |

## Limits

- zypper does not tell which updates are security updates: use scope "all" or "packages" there.
- Updates are planned from the metadata the host last fetched; the patch step refreshes first.
- A host whose last scan could not list its updates is left out of a plan, with that reason.
  Rescan it. The Posture page shows its update counts as "?".
- Reboot detection on RHEL-family hosts uses `needs-restarting -r`, or the dnf plugin of the same
  name when the command is not installed (minimal EL9). Without either, only the kernel is compared.
- MCP tools can read rollouts. They cannot create, approve or change one.
