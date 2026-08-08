-- SeyalRun v2.0 — Phase 1 schema (PostgreSQL)
--
-- Two databases share one engine (DB_ENGINE=postgres):
--   seyalrun_identity   — run this file against it (identity tables)
--   seyalrun_inventory  — run this file against it (inventory tables)
--
-- The same file defines both sets of tables; ops/init-db.sh imports the
-- whole file into BOTH databases. Each service's Alembic migrations only
-- touch the tables that belong to that service (CREATE TABLE IF NOT EXISTS
-- makes both paths idempotent and order-independent).

-- ============================================================================
-- IDENTITY DB TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS za_roles (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL UNIQUE,
    description TEXT         NOT NULL DEFAULT '',
    permissions JSONB        NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_user_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_users (
    id            VARCHAR(36)  PRIMARY KEY,
    username      VARCHAR(100) NOT NULL UNIQUE,
    display_name  VARCHAR(200) NOT NULL DEFAULT '',
    email         VARCHAR(200) NOT NULL DEFAULT '',
    zabbix_userid VARCHAR(50),
    role_id       VARCHAR(36)  REFERENCES za_roles(id) ON DELETE SET NULL,
    password_hash VARCHAR(255) NOT NULL DEFAULT '',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_user_group_members (
    user_id  VARCHAR(36) NOT NULL REFERENCES za_users(id) ON DELETE CASCADE,
    group_id VARCHAR(36) NOT NULL REFERENCES za_user_groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS za_sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL REFERENCES za_users(id) ON DELETE CASCADE,
    jwt_id      VARCHAR(36)  NOT NULL UNIQUE,
    ip_address  VARCHAR(64)  NOT NULL DEFAULT '',
    user_agent  VARCHAR(255) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ  NOT NULL,
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_za_sessions_user_id ON za_sessions(user_id);

-- PAM: (user OR user_group) -> (host OR host_group, in inventory DB) -> credential -> actions.
-- host_id / host_group_id / credential_id reference inventory-service rows by id (no cross-db FK).
CREATE TABLE IF NOT EXISTS za_authorizations (
    id              VARCHAR(36)  PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    user_id         VARCHAR(36)  REFERENCES za_users(id) ON DELETE CASCADE,
    user_group_id   VARCHAR(36)  REFERENCES za_user_groups(id) ON DELETE CASCADE,
    host_id         VARCHAR(36),
    host_group_id   VARCHAR(36),
    credential_id   VARCHAR(36),
    actions         JSONB        NOT NULL DEFAULT '[]',
    date_start      TIMESTAMPTZ,
    date_expired    TIMESTAMPTZ,
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_za_authz_user_id ON za_authorizations(user_id);
CREATE INDEX IF NOT EXISTS ix_za_authz_user_group_id ON za_authorizations(user_group_id);
CREATE INDEX IF NOT EXISTS ix_za_authz_host_id ON za_authorizations(host_id);
CREATE INDEX IF NOT EXISTS ix_za_authz_host_group_id ON za_authorizations(host_group_id);

-- Named regex pattern sets ("Command Groups" in JumpServer terms).
CREATE TABLE IF NOT EXISTS za_command_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL DEFAULT '',
    patterns    JSONB        NOT NULL DEFAULT '[]',
    match_type  VARCHAR(20)  NOT NULL DEFAULT 'regex',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- SSH ACL: links a command group to a scope (user/group x host/host-group)
-- with allow|deny|confirm semantics + priority. Model + CRUD ship in Phase 1;
-- live enforcement lands with terminal-service in Phase 2.
CREATE TABLE IF NOT EXISTS za_command_filters (
    id                VARCHAR(36)  PRIMARY KEY,
    name              VARCHAR(200) NOT NULL,
    command_group_id  VARCHAR(36)  NOT NULL REFERENCES za_command_groups(id) ON DELETE CASCADE,
    user_id           VARCHAR(36)  REFERENCES za_users(id) ON DELETE CASCADE,
    user_group_id     VARCHAR(36)  REFERENCES za_user_groups(id) ON DELETE CASCADE,
    host_id           VARCHAR(36),
    host_group_id     VARCHAR(36),
    action            VARCHAR(20)  NOT NULL DEFAULT 'deny',  -- allow|deny|confirm
    priority          INTEGER      NOT NULL DEFAULT 50,
    enabled           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_za_cmdfilter_group ON za_command_filters(command_group_id);

-- Login ACL: time-window / IP / host restrictions per user or group.
-- Model + CRUD ship in Phase 1; enforcement (api-gateway + terminal-service) in Phase 2.
CREATE TABLE IF NOT EXISTS za_login_acls (
    id            VARCHAR(36)  PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    user_id       VARCHAR(36)  REFERENCES za_users(id) ON DELETE CASCADE,
    user_group_id VARCHAR(36)  REFERENCES za_user_groups(id) ON DELETE CASCADE,
    ip_cidr       VARCHAR(64),
    time_start    VARCHAR(5),   -- "HH:MM", null = no time restriction
    time_end      VARCHAR(5),
    days_of_week  JSONB        NOT NULL DEFAULT '[0,1,2,3,4,5,6]',
    action        VARCHAR(10)  NOT NULL DEFAULT 'allow',  -- allow|deny
    priority      INTEGER      NOT NULL DEFAULT 50,
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Personal Access Tokens — Argon2id hash + API_TOKEN_PEPPER, shown once.
CREATE TABLE IF NOT EXISTS za_api_tokens (
    id           VARCHAR(36)  PRIMARY KEY,
    user_id      VARCHAR(36)  NOT NULL REFERENCES za_users(id) ON DELETE CASCADE,
    name         VARCHAR(200) NOT NULL,
    token_hash   VARCHAR(255) NOT NULL,
    token_prefix VARCHAR(12)  NOT NULL,
    scopes       JSONB        NOT NULL DEFAULT '["read"]',
    expires_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_za_api_tokens_user_id ON za_api_tokens(user_id);
CREATE INDEX IF NOT EXISTS ix_za_api_tokens_prefix ON za_api_tokens(token_prefix);

CREATE TABLE IF NOT EXISTS za_audit_logs (
    id            VARCHAR(36)  PRIMARY KEY,
    user_id       VARCHAR(36),
    username      VARCHAR(100) NOT NULL DEFAULT '',
    action        VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50)  NOT NULL DEFAULT '',
    resource_id   VARCHAR(36)  NOT NULL DEFAULT '',
    details       JSONB        NOT NULL DEFAULT '{}',
    ip_address    VARCHAR(64)  NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_za_audit_created_at ON za_audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_za_audit_user_id ON za_audit_logs(user_id);

-- Seed default roles (idempotent).
INSERT INTO za_roles (id, name, description, permissions)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'superadmin', 'Full access, including Settings/Integrations', '["*"]'),
    ('00000000-0000-0000-0000-000000000002', 'admin', 'Manage assets, credentials, automation', '["assets:*","credentials:*","automation:*"]'),
    ('00000000-0000-0000-0000-000000000003', 'user', 'Workbench access to authorized hosts only', '["workbench:use"]')
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- INVENTORY DB TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS za_zones (
    id             VARCHAR(36)  PRIMARY KEY,
    name           VARCHAR(200) NOT NULL UNIQUE,
    description    TEXT         NOT NULL DEFAULT '',
    parent_zone_id VARCHAR(36)  REFERENCES za_zones(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_gateways (
    id            VARCHAR(36)  PRIMARY KEY,
    zone_id       VARCHAR(36)  REFERENCES za_zones(id) ON DELETE CASCADE,
    name          VARCHAR(200) NOT NULL,
    host          VARCHAR(255) NOT NULL,
    port          INTEGER      NOT NULL DEFAULT 22,
    username      VARCHAR(100) NOT NULL DEFAULT '',
    credential_id VARCHAR(36),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_host_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_hosts (
    id              VARCHAR(36)  PRIMARY KEY,
    zabbix_hostid   VARCHAR(50)  UNIQUE,
    name            VARCHAR(200) NOT NULL,
    ip              VARCHAR(100) NOT NULL,
    port            INTEGER      NOT NULL DEFAULT 22,
    os_type         VARCHAR(20)  NOT NULL DEFAULT 'linux',
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    zone_id         VARCHAR(36)  REFERENCES za_zones(id) ON DELETE SET NULL,
    last_synced_at  TIMESTAMPTZ,
    is_reachable    BOOLEAN,
    last_ping_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_host_group_members (
    host_id  VARCHAR(36) NOT NULL REFERENCES za_hosts(id) ON DELETE CASCADE,
    group_id VARCHAR(36) NOT NULL REFERENCES za_host_groups(id) ON DELETE CASCADE,
    PRIMARY KEY (host_id, group_id)
);

-- Account Templates — defaults applied when creating a new credential.
CREATE TABLE IF NOT EXISTS za_credential_templates (
    id              VARCHAR(36)  PRIMARY KEY,
    name            VARCHAR(200) NOT NULL UNIQUE,
    secret_type     VARCHAR(20)  NOT NULL DEFAULT 'password',  -- password|ssh_key|vault_path
    description     TEXT         NOT NULL DEFAULT '',
    default_username VARCHAR(100) NOT NULL DEFAULT '',
    default_params  JSONB        NOT NULL DEFAULT '{}',
    push_enabled    BOOLEAN      NOT NULL DEFAULT FALSE,
    rotation_days   INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Credentials — secret_ciphertext is AES-256-GCM (libs/dbcore.crypto), never plaintext.
CREATE TABLE IF NOT EXISTS za_credentials (
    id                VARCHAR(36)  PRIMARY KEY,
    name              VARCHAR(200) NOT NULL DEFAULT '',
    template_id       VARCHAR(36)  REFERENCES za_credential_templates(id) ON DELETE SET NULL,
    username          VARCHAR(100) NOT NULL,
    secret_type       VARCHAR(20)  NOT NULL DEFAULT 'password',  -- password|ssh_key|vault_path
    secret_ciphertext TEXT         NOT NULL,
    credential_scope  VARCHAR(20)  NOT NULL DEFAULT 'host',       -- host|template
    is_default        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS za_credential_host_links (
    credential_id VARCHAR(36) NOT NULL REFERENCES za_credentials(id) ON DELETE CASCADE,
    host_id       VARCHAR(36) NOT NULL REFERENCES za_hosts(id) ON DELETE CASCADE,
    PRIMARY KEY (credential_id, host_id)
);
