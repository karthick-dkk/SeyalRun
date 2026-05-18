# ADR-001: PostgreSQL instead of MySQL

**Date:** 2026-05-16
**Status:** Accepted

## Context

The README specifies MySQL 8 as the JumpServer database. When Server 1 was inspected via SSH, the JumpServer installer (v4.10.16-ce) had deployed PostgreSQL 16 as the database engine (`DB_ENGINE=postgresql` in config.txt). JumpServer's installer defaults changed between versions — PostgreSQL is now the default for new installations.

## Decision

Keep PostgreSQL 16. Do not attempt to migrate to MySQL.

## Rationale

- PostgreSQL is already running and JumpServer is healthy
- Migrating to MySQL would require a full data migration, downtime, and testing
- PostgreSQL 16 is a well-supported, production-grade database
- JumpServer officially supports both MySQL and PostgreSQL
- All Ansible playbooks and documentation updated to reflect PostgreSQL

## Consequences

- Any MySQL-specific instructions in the README must be updated
- Ansible Vault references `vault_db_password` applies to PostgreSQL
- Backup playbooks use `pg_dump` not `mysqldump`
