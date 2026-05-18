# Pravesh

**A Privileged Access Management platform built on a JumpServer fork, integrated with FreeIPA, Active Directory, Zabbix, Ansible, and Salt-SSH.**

> Status: Project specification (v0.1)
> Upstream base: JumpServer v4.10.13 LTS (Nov 2025)
> License: GPLv3 (inherited from upstream)
> Owner: Karthi

---

## 1. Project Overview

Pravesh is a learning/lab project that takes the open-source JumpServer PAM platform and extends it into a complete privileged-access workbench tailored for a real DevSecOps environment: AWS-hosted Linux fleet, ELK/Wazuh observability stack, Zabbix 7.0 LTS monitoring, and SaltStack-managed configuration.

The project is structured as a **rebranded fork plus a constellation of sidecar services and plugins**. The fork itself stays minimally divergent from upstream so that LTS security patches can be merged without conflict. All meaningful functionality lives in services that talk to JumpServer (and Zabbix, and FreeIPA) through their public APIs.

The scope covers six headline features:

1. Ansible and Salt-SSH automation jobs triggered from the bastion.
2. FreeIPA deployment automation and an admin UI for day-2 management.
3. Linux Active Directory user management (via FreeIPA-AD trust).
4. Zabbix asset sync with live metrics rendered inside the bastion UI.
5. A Zabbix plugin that launches a JumpServer session for any monitored host.
6. A JumpServer plugin that keeps Zabbix hosts and JumpServer assets in lockstep.

---

## 2. Learning Objectives

The project is a controlled environment to practise DevSecOps-grade work end-to-end: API integration between disparate platforms, identity federation at the Linux/AD/IPA boundary, infrastructure-as-code for both deployment and ongoing operations, sidecar-pattern microservice design, and the operational discipline of running a long-lived fork of an active upstream.

It also doubles as portfolio material: each phase produces a working component plus a write-up suitable for LinkedIn, Medium, or a Tamil-language YouTube walkthrough.

---

## 3. Architecture Principles

Three rules govern every design decision.

**Core is untouchable.** Nothing in `jumpserver/jumpserver` Django apps, `koko/`, `lion/`, `chen/`, or `razor/` gets patched. The only files modified inside the fork are branding assets, theme variables, and i18n strings. Every behavioural change happens outside the fork.

**API-first integration.** All cross-platform glue goes through documented REST APIs — JumpServer's `/api/v1/`, Zabbix's `api_jsonrpc.php`, FreeIPA's JSON-RPC, and Active Directory via LDAPS/Kerberos. No direct database access, no shared filesystems, no symlinks into other services' code trees.

**Sidecar everything.** New functionality is deployed as standalone services in their own containers, with their own repos, lifecycle, and version. JumpServer remains a stock LTS deployment that happens to have friends.

---

## 4. System Architecture

```
                           ┌──────────────────────────────────────┐
                           │           Pravesh Stack         │
                           └──────────────────────────────────────┘

   ┌─────────────┐         ┌──────────────────────┐        ┌──────────────────┐
   │   Admins    │────────▶│   JumpServer (fork)  │◀──────▶│  FreeIPA Server  │
   │  (browser)  │         │   Lina + Luna + Core │  LDAP  │   + AD trust     │
   └─────────────┘         └──────────┬───────────┘        └────────┬─────────┘
                                      │ REST API                    │ Kerberos
                                      │                             │
                ┌─────────────────────┼──────────────────────────┐  │
                │                     │                          │  │
                ▼                     ▼                          ▼  ▼
      ┌──────────────────┐  ┌───────────────────┐      ┌──────────────────────┐
      │  zbx-sync        │  │  automation-bridge│      │  Managed Linux fleet │
      │  sidecar         │  │  (Ansible/SaltSSH)│      │  (~300 AWS hosts)    │
      └────────┬─────────┘  └─────────┬─────────┘      └──────────┬───────────┘
               │ JSON-RPC             │ ssh                       │
               ▼                      ▼                           │
      ┌──────────────────┐  ┌───────────────────┐                 │
      │  Zabbix Server   │  │  Salt-SSH runner  │                 │
      │  + Frontend      │  │  + Ansible runner │─────────────────┘
      │  (with plugin)   │  └───────────────────┘
      └──────────────────┘
```

Three planes:

- **Identity plane** — FreeIPA is authoritative for human and service identity. AD is trusted (one-way) so Windows-domain users can SSH into Linux hosts. JumpServer authenticates against FreeIPA over LDAP and inherits group membership.
- **Access plane** — JumpServer terminates all interactive sessions. No direct SSH from anywhere except the bastion. Session recordings ship to S3.
- **Operations plane** — Ansible and Salt-SSH execute against the fleet *through the bastion's audit boundary*, so every change is attributable and replayable.

---

## 5. Feature Specifications

### 5.1 Ansible and Salt-SSH Automation

**Goal.** Run Ansible playbooks and Salt-SSH commands from the JumpServer UI against asset groups, with full output captured in the audit log.

**Approach.** JumpServer 4.x already has a native Jobs feature that executes Ansible adhoc and playbook tasks. We use it as-is for Ansible. For Salt-SSH — which JumpServer does not support natively — we add a sidecar service called `automation-bridge` that exposes a small HTTP API. JumpServer Jobs are configured to call `automation-bridge` as a shell command target; the bridge runs `salt-ssh` against the specified roster and streams output back, which JumpServer captures into the session log.

**Why Salt-SSH alongside Ansible.** Salt-SSH gives you Salt's state files and grains-based targeting without requiring a minion agent on every host. Useful for ephemeral instances, jump targets in client environments where you can't install agents, or rapid one-shot remediation. Ansible is for orchestration and playbook-driven changes; Salt-SSH is for grain-targeted ad-hoc work and state enforcement on agent-less hosts.

**Components.**

- `automation-bridge/` — FastAPI service.
  - `POST /run/ansible` — accepts playbook name, inventory selector, vars; returns job ID and streams output via SSE.
  - `POST /run/salt-ssh` — accepts target glob, function (e.g. `state.apply`), args; returns job ID and stream.
  - `GET /jobs/{job_id}` — fetch result for replay.
- `playbooks/` — Ansible repo (separate Git remote), versioned and signed.
- `salt/` — Salt states tree, shared with the existing SaltStack environment.
- `roster.yaml` — Salt-SSH roster, generated from JumpServer assets by the `zbx-sync` sidecar's sister tool `roster-gen`.

**Deliverables.** A working `automation-bridge` service, three sample playbooks (patch-now, audit-sshd, deploy-filebeat), three Salt states for one-shot use (rotate-keys, kill-session, snapshot-disk), and a JumpServer Job template for each one bound to asset groups.

---

### 5.2 FreeIPA Deployment and Management

**Goal.** Deploy a production-grade FreeIPA server (with replica) and provide a streamlined UI for the day-to-day tasks that the stock FreeIPA web UI makes tedious.

**Approach.** Two parts. **Deployment** is automated end-to-end with an Ansible playbook that uses the official `freeipa.ansible_freeipa` collection. The playbook provisions the primary server, a replica in a different AZ, configures DNS (integrated BIND), sets up the trust with AD (see 5.3), and enrolls a baseline of test clients. **Management** is delivered by a small Vue.js sidecar app called `ipa-console` that wraps the FreeIPA JSON-RPC API and focuses on the five operations you actually do daily: user create/disable, group membership change, HBAC rule edit, sudo rule edit, and host enrollment status.

**Components.**

- `playbooks/freeipa-deploy.yml` — green-field deployment.
- `playbooks/freeipa-replica.yml` — adds a replica.
- `playbooks/freeipa-client-enroll.yml` — joins a Linux host (uses ipaclient role with one-time-password from a Vault secret).
- `ipa-console/` — Vue 3 + Vite SPA, served from the same Nginx that fronts the bastion, authenticates via JumpServer's OIDC provider (which is itself federated to FreeIPA).

**Integration points.** JumpServer's LDAP backend points at `ipa.bastion.lab.example.com`. JumpServer asset accounts are populated from FreeIPA host enrollment status so the assets list and the IPA hosts list are guaranteed consistent.

**Deliverables.** A reproducible one-command FreeIPA stand-up, a documented disaster-recovery procedure (restore from backup, promote replica), and a working `ipa-console` covering the five core operations.

---

### 5.3 Linux Active Directory User Management

**Goal.** Allow Active Directory users to authenticate to Linux hosts (via JumpServer) without having local Linux accounts, with group-based authorization.

**Approach.** A one-way trust from FreeIPA to Active Directory. AD remains authoritative for Windows-domain identity; FreeIPA trusts AD's Kerberos realm and presents AD users as IPA-recognised principals. Linux clients enrolled with `ipa-client-install` automatically resolve AD users via SSSD without any per-host AD join.

The "user management" piece of the feature is the *governance layer on top* — a small UI in `ipa-console` that lets an admin select an AD group and grant it HBAC access to a category of Linux hosts (e.g., `AD\Linux-Admins-Prod` → HBAC rule allowing `ssh` to host group `prod-linux`). The actual user records stay in AD; FreeIPA holds the access policy; JumpServer enforces the session boundary.

**Components.**

- `playbooks/ipa-ad-trust.yml` — establishes the trust, configures DNS forwarding, validates with `id user@AD.DOMAIN`.
- `ipa-console/src/views/AdAccess.vue` — UI for AD-group-to-host-group grants.
- `docs/ad-onboarding.md` — runbook covering DNS prerequisites, certificate exchange, and trust validation.

**Why not direct AD join with realmd/SSSD.** Direct join is simpler for one host but doesn't scale: every host has its own keytab, group policies are messy on Linux, and you have no centralized way to revoke. With IPA-AD trust, AD revocation propagates automatically, sudo and HBAC rules live in one place, and host enrollment becomes a single Ansible task.

**Deliverables.** A working trust between a lab AD (Samba AD-DC is fine for the lab) and FreeIPA, demonstrated end-to-end with an AD user SSHing into a Linux host through JumpServer using AD credentials.

---

### 5.4 Zabbix Asset Sync with Live Metrics

**Goal.** Inside the JumpServer asset detail view, show live CPU, memory, disk, and network metrics for the asset pulled from Zabbix.

**Approach.** Two-part. Asset existence is kept in sync by the `zbx-sync` sidecar (described separately in 5.6). The live metrics view is implemented as a **Grafana panel embedded in JumpServer via an iframe**, with the asset hostname passed as a template variable. Grafana uses the Zabbix datasource plugin (official, mature) and pre-built dashboards filter to `{host=$asset}`.

The integration into the JumpServer UI is done without forking Lina. JumpServer supports "External Applications" — a configurable URL that opens with templated parameters. We register `https://grafana.bastion.lab.example.com/d/host-overview?var-host={{ asset.hostname }}&kiosk=tv` as a per-asset application. From the asset detail page it opens as a tab; from the asset list it's a row-action.

For a richer experience (in-page rather than tab-out) we additionally provide a small Vue widget `metrics-strip` that renders four sparklines (CPU, mem, disk, net) by polling Zabbix's `history.get` API every 15 seconds. This widget is hosted on the `ipa-console` Nginx and pulled into JumpServer via the External Applications iframe mechanism on the dashboard.

**Components.**

- `grafana/dashboards/host-overview.json` — Grafana dashboard, version-controlled.
- `grafana/provisioning/` — datasource and dashboard provisioning files.
- `metrics-strip/` — small Vue widget, 4 sparklines, polls Zabbix.
- `playbooks/grafana-deploy.yml` — Ansible deployment of Grafana with the Zabbix plugin pre-configured.

**Deliverables.** Live metrics visible for any synced asset within 30 seconds of clicking on it, with at least four core panels (CPU steal-aware, memory, disk IO, network throughput) and configurable timerange.

---

### 5.5 Zabbix Plugin: "Connect via Bastion"

**Goal.** From the Zabbix frontend, when looking at any monitored Linux host, an admin can click a button and land in a JumpServer SSH session for that host — no copy-paste of IPs, no separate login.

**Approach.** Zabbix 7.0 supports custom **Frontend Modules**, which can register menu entries on the host page. We build a small module `zbx-bastion-launch` that adds a "Connect via Bastion" entry to the host context menu. Clicking it calls a Zabbix-side endpoint that maps the Zabbix `hostid` to a JumpServer `asset_id` (using the same mapping the `zbx-sync` sidecar maintains in Redis), requests a short-lived launch token from the bastion, and redirects the browser to JumpServer's Luna terminal with the token in the URL.

The flow:

1. User clicks "Connect via Bastion" on Zabbix host page.
2. Zabbix module calls `https://bastion.../api/v1/launch-tokens/` with `{zabbix_hostid: 12345}` and the user's current Zabbix session cookie.
3. The bastion's `launch-token` sidecar verifies the user via Zabbix SSO (both bastion and Zabbix federate to FreeIPA), looks up the asset, mints a one-time JWT, and returns a Luna URL.
4. Zabbix module redirects the browser. Luna validates the token, opens an SSH session.

**Components.**

- `zbx-bastion-launch/` — Zabbix Frontend Module (PHP, namespaced per Zabbix 7.x module standard).
- `launch-token/` — sidecar service, FastAPI, issues and validates one-time tokens.
- Shared Redis instance for hostid→asset_id mapping.

**Deliverables.** Working Zabbix module installable via Zabbix's module manager, a documented installation procedure, and a screencast showing the click-to-session flow end-to-end.

---

### 5.6 JumpServer Plugin: Zabbix Host Sync

**Goal.** Keep the JumpServer asset inventory automatically synchronized with Zabbix host inventory, including host groups, tags, and connection details.

**Approach.** A sidecar service `zbx-sync` that runs on a schedule (every 5 minutes, configurable) and on demand via webhook from Zabbix. It pulls hosts from Zabbix via `host.get` (with `selectInterfaces`, `selectHostGroups`, `selectTags`), pulls assets from JumpServer via `/api/v1/assets/hosts/`, computes the diff, and applies changes through JumpServer's API. It also maintains the bidirectional hostid↔asset_id mapping that the launch-token service uses.

Mapping rules are config-driven. Zabbix host groups map to JumpServer asset nodes (a tree). Zabbix tags map to JumpServer labels. Connection method (SSH vs WinRM vs DB) is inferred from a Zabbix tag or template name.

Sync is one-directional by default (Zabbix → JumpServer) since Zabbix is where ops/SRE teams already manage hosts. Bidirectional is opt-in for greenfield deployments.

**Components.**

- `zbx-sync/` — Python service. `apscheduler` for cron, `httpx` for both APIs, Pydantic models for assets and hosts, structured logs to stdout for Filebeat to pick up.
- `zbx-sync/config.example.yaml` — mapping rules, scheduling, conflict policy.
- `webhook-receiver/` — small FastAPI endpoint that Zabbix actions hit on host create/disable.

**Mapping example.**

```yaml
group_mapping:
  - zabbix: "Production/Linux/MySQL"
    jumpserver_node: "/PROD/DB/MySQL"
  - zabbix: "Production/Linux/Web"
    jumpserver_node: "/PROD/Web"
tag_mapping:
  source_field: "tag"
  target_field: "label"
connection_inference:
  default: "ssh"
  by_template:
    "Template OS Windows by WMI": "winrm"
    "Template DB MySQL by Zabbix agent": "mysql"
conflict_policy: "zabbix_wins"
```

**Deliverables.** A daemonized sync service with Prometheus metrics, an admin command to do a dry-run diff, and an audit log of every change applied.

---

## 6. Repository Structure

A monorepo for the platform and one separate repo per service that has an independent lifecycle (the JumpServer fork stays separate so it can track upstream cleanly).

```
pravesh/                       # this org's primary repo
├── README.md                       # this document
├── docs/
│   ├── adr/                        # architecture decision records
│   ├── runbooks/                   # ops runbooks
│   └── onboarding.md
├── infra/
│   ├── terraform/                  # AWS infra (Lightsail/EC2, R53, S3)
│   ├── ansible/                    # platform playbooks (excluding feature playbooks)
│   └── salt/                       # salt states for the bastion itself
├── docker/
│   ├── compose.bastion.yml         # the JumpServer fork deployment
│   ├── compose.sidecars.yml        # all sidecar services
│   └── compose.observability.yml   # grafana, filebeat, etc
├── services/
│   ├── automation-bridge/
│   ├── zbx-sync/
│   ├── launch-token/
│   ├── ipa-console/
│   ├── metrics-strip/
│   └── webhook-receiver/
├── plugins/
│   └── zbx-bastion-launch/         # Zabbix frontend module
├── playbooks/
│   ├── freeipa-deploy.yml
│   ├── freeipa-replica.yml
│   ├── freeipa-client-enroll.yml
│   ├── ipa-ad-trust.yml
│   ├── grafana-deploy.yml
│   ├── audit-sshd.yml
│   └── patch-now.yml
├── salt/
│   └── states/
│       ├── rotate-keys/
│       ├── kill-session/
│       └── snapshot-disk/
└── grafana/
    ├── dashboards/
    └── provisioning/

pravesh-fork/                  # separate repo, tracks upstream/master
├── (upstream JumpServer tree)
└── brand/pravesh/                     # brand/pravesh branch contains assets only
```

---

## 7. Technology Stack

The bastion fork itself runs Python 3.11 (Django 4.x), Go 1.22 (Koko, Lion), Vue 3 (Lina, Luna), MySQL 8, Redis 7, and Nginx. We don't change any of that.

The sidecar services standardize on Python 3.12 with FastAPI for HTTP services, APScheduler for cron-like tasks, httpx for clients, Pydantic for models, and structlog for logging. The one Zabbix plugin is PHP 8.2 because that's what Zabbix 7.0 requires. The two frontend admin UIs (ipa-console, metrics-strip) are Vue 3 + Vite + TypeScript.

Infrastructure is AWS — EC2 or Lightsail instances for the bastion, RDS for the JumpServer database in production, S3 for session recordings, Route53 for DNS, and ACM for public certificates. Cloudflare sits in front for DDoS and bot management, with Origin Certificates pinned end-to-end. Containers run via Docker Compose on a single host for the lab; the production blueprint moves to a small EKS cluster with the same compose-equivalent Helm values.

Observability ties into the existing observability stack — Filebeat ships logs to Elasticsearch, Wazuh agents on all sidecars feed the SIEM, and Zabbix monitors every component of the bastion itself (its own circular dependency is intentional — the bastion's health is visible from the same place as everything else).

---

## 8. Infrastructure Requirements

For the lab environment, a single AWS Lightsail instance at 4 vCPU / 16 GB / 80 GB SSD is sufficient to run the JumpServer stack, all sidecars, FreeIPA primary, Samba AD-DC, and a handful of target Linux hosts as Lightsail micros. The Grafana stack runs as containers on the same host.

For a production-like blueprint, the layout splits to a t3.large for JumpServer Core + Lina + Luna, a t3.medium for connectors (Koko/Lion/Chen), a separate t3.medium for sidecars, a dedicated t3.small for FreeIPA primary plus a replica in another AZ, and RDS MySQL 8.0 (db.t3.small) for JumpServer's database. Total monthly cost lands around USD 180-220 in ap-south-1, before any S3 or data transfer.

DNS: one subdomain per service under the lab zone (`bastion.lab.example.com`, `ipa.lab.example.com`, `grafana.lab.example.com`, `zabbix.lab.example.com`). Cloudflare proxies all of them; Origin Certificates protect the transport between Cloudflare and the AWS instance.

---

## 9. Implementation Roadmap

Twelve weeks, five phases. The phasing is deliberate: identity first, then automation, then observability bridging, then plugins, then hardening. Each phase ends with something demoable and a blog-post-shaped writeup.

**Phase 0 — Foundation (Week 1).** Provision AWS infrastructure with Terraform. Set up DNS, certificates, and Cloudflare. Fork JumpServer, establish the upstream tracking workflow, do a vanilla deployment. Confirm SSH/RDP to a single test asset works.

**Phase 1 — Identity (Weeks 2–3).** Deploy FreeIPA primary and replica via Ansible. Stand up Samba AD-DC as a lab AD. Establish IPA-AD trust. Federate JumpServer authentication to FreeIPA over LDAP. Build the first cut of `ipa-console` covering user, group, and host operations. Demo: AD user logs into JumpServer using AD credentials, then SSHes to a Linux host where they have no local account.

**Phase 2 — Automation (Weeks 4–5).** Build `automation-bridge`. Wire up the Ansible runner (using the official `ansible-runner` library). Wire up the Salt-SSH runner. Register both as JumpServer Job adapters. Author the three baseline playbooks and three baseline Salt states. Demo: kick off "patch-now" against a tagged asset group from the JumpServer UI, watch live output, replay the audit log.

**Phase 3 — Observability Bridge (Weeks 6–7).** Deploy Grafana with the Zabbix datasource plugin. Author the host-overview dashboard. Register Grafana as a JumpServer External Application with the asset hostname templated. Build `metrics-strip` for the lighter inline view. Demo: click any asset in the bastion, see live metrics inside 5 seconds.

**Phase 4 — Plugins (Weeks 8–10).** Build `zbx-sync` for the JumpServer-side sync. Build the Zabbix frontend module for "Connect via Bastion". Build `launch-token`. Wire them together with the Redis hostid↔asset_id mapping. Demo: from any Zabbix host page, click through to a JumpServer session in a single browser navigation.

**Phase 5 — Hardening and Docs (Weeks 11–12).** Threat-model the whole platform (your ethical hacking skills get a workout here). Run a Nessus/Nikto scan against the public surface; fix findings. Encrypt sidecar-to-sidecar traffic with mTLS. Implement Prometheus metrics on every service. Write the runbooks: backup/restore of FreeIPA, JumpServer upgrade procedure, sidecar redeployment, disaster recovery. Publish the project as a series of LinkedIn/Medium posts in English plus a Tamil-language video walkthrough.

---

## 10. Security and Compliance

The platform handles privileged credentials and session recordings of administrative access — it is itself a Tier-0 system. Three security-design commitments shape the project.

**Credential isolation.** No service-to-service shared secrets in environment variables. FreeIPA-managed service principals with Kerberos keytabs for inter-service auth where possible; short-lived JWTs minted by `launch-token` for human-facing flows. Zoho Vault stores all break-glass credentials (or matched to your existing vault choice); HashiCorp Vault is an option for greenfield.

**Audit completeness.** Every privileged action produces a record in at least two of: JumpServer's session log, Wazuh's audit channel, and Elasticsearch. Sessions are recorded to S3 with object-lock enabled for a 1-year retention. The sidecars log structured JSON, picked up by Filebeat, indexed in a dedicated `bastion-*` index pattern.

**GPLv3 compliance.** Because the fork is GPLv3, anything that links into the JumpServer codebase or is distributed alongside it must also be GPLv3-licensed when distributed externally. The sidecar architecture deliberately keeps custom code at arm's length — they're independent services that talk over HTTP, not derivative works. The Zabbix plugin lives in its own repo and is licensed AGPLv3 (matching Zabbix's licensing). The brand assets remain proprietary to the deploying organisation and are not redistributed.

Data residency: all data, including session recordings, stays in `ap-south-1` (Mumbai) for compliance with relevant Indian data-protection expectations and applicable client contracts.

---

## 11. Risks and Mitigations

The biggest risk is **upstream drift**. JumpServer releases LTS patches frequently and a year of accumulated brand-branch commits can become painful to rebase if changes creep beyond assets. Mitigated by the discipline rule (no code mods in the fork), a weekly upstream-fetch habit, and a CI job that fails the build if the fork diff includes any `*.py` or `*.go` files outside known asset paths.

The second risk is **integration sprawl** — six interconnected sidecars is a lot of moving parts for a side project. Mitigated by treating Phase 1 (identity) as the only mandatory dependency for all later phases, and by stubbing missing services with `nginx -> static JSON` mocks during early development.

The third is the **lab-to-production gap**. Mitigated by writing Ansible playbooks for the lab from day one — the same playbooks deploy production with different variable files.

---

## 12. Success Metrics

The project is "done" when an AD user can authenticate to JumpServer using AD credentials, see a list of Linux assets synchronized from Zabbix, click into one and see live metrics, open an SSH session, run an Ansible playbook against a group of those assets, and complete the same loop in reverse by clicking "Connect via Bastion" from the Zabbix host page — all without anyone touching a `.py` or `.go` file inside the JumpServer fork.

Quantified targets:

- Upstream merge latency: less than 1 working day from JumpServer LTS release to merged-and-deployed.
- Asset sync latency: less than 5 minutes from Zabbix change to JumpServer reflection.
- Session-to-metrics latency: less than 5 seconds from asset click to first metrics render.
- Audit completeness: 100% of privileged sessions in both JumpServer recordings and Wazuh alerts.
- Mean time to revoke an AD user's Linux access: less than 60 seconds (the AD-side disable plus the next FreeIPA cache flush).

---

## 13. Appendix

### A. JumpServer API endpoints used

- `GET /api/v1/assets/hosts/` — list assets
- `POST /api/v1/assets/hosts/` — create asset
- `PATCH /api/v1/assets/hosts/{id}/` — update asset
- `GET /api/v1/assets/nodes/` — list asset tree nodes
- `POST /api/v1/assets/nodes/` — create node
- `GET /api/v1/audits/sessions/` — session audit
- `POST /api/v1/authentication/connection-token/` — mint a session token (the basis for `launch-token`)

### B. Zabbix API methods used

- `host.get` — with `selectInterfaces`, `selectHostGroups`, `selectTags`, `selectParentTemplates`
- `hostgroup.get` — for the group mapping pass
- `history.get` — for `metrics-strip` sparklines (itemid + time_from)
- `item.get` — to resolve metric keys per host
- `user.checkAuthentication` — for the launch flow's user verification

### C. FreeIPA Ansible role highlights

The `freeipa.ansible_freeipa` collection ships roles for `ipaserver`, `ipareplica`, `ipaclient`, plus modules for `ipauser`, `ipagroup`, `ipahost`, `ipahbacrule`, `ipasudorule`, `ipatrust`. The deployment playbook uses `ipaserver` and `ipareplica`; the trust playbook uses `ipatrust`; the day-2 modules wrap up into Ansible roles that the `ipa-console` UI invokes through `ansible-runner`.

### D. Brand-surface file inventory (the only files the fork modifies)

In `jumpserver/jumpserver`:

- `apps/static/img/login_image.png`, `apps/static/img/logo.png`, `apps/static/img/logo_text.png`, `apps/static/img/favicon.ico`
- `apps/i18n/core/en/LC_MESSAGES/django.po` and `zh/...` — string "JumpServer" → "Pravesh"
- `apps/jumpserver/conf.py` reads `SITE_URL`, `EMAIL_HOST_USER`, `INTERFACE` settings from env

In `jumpserver/lina`:

- `src/assets/img/logo*.png`, `public/favicon.ico`
- `src/styles/variables.scss` — primary/secondary color variables

In `jumpserver/luna`:

- `src/assets/img/`, `public/favicon.ico`
- `src/styles/_variables.scss`



---

*End of specification v0.1. Open questions, design changes, and ADRs go into `docs/adr/` once the repo is bootstrapped.*
