-- SeyalRun v2.0 — Phase 1 schema (MySQL 8.0+)
--
-- Two databases share one engine (DB_ENGINE=mysql):
--   seyalrun_identity   — run this file against it (identity tables)
--   seyalrun_inventory  — run this file against it (inventory tables)
--
-- The same file defines both sets of tables; ops/init-db.sh imports the
-- whole file into BOTH databases. CREATE TABLE IF NOT EXISTS + inline
-- indexes make this idempotent and safe to re-run.

-- ============================================================================
-- IDENTITY DB TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS za_roles (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL UNIQUE,
    description TEXT         NOT NULL,
    permissions JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_user_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_users (
    id            VARCHAR(36)  PRIMARY KEY,
    username      VARCHAR(100) NOT NULL UNIQUE,
    display_name  VARCHAR(200) NOT NULL DEFAULT '',
    email         VARCHAR(200) NOT NULL DEFAULT '',
    zabbix_userid VARCHAR(50),
    role_id       VARCHAR(36),
    password_hash VARCHAR(255) NOT NULL DEFAULT '',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY ix_za_users_role_id (role_id),
    CONSTRAINT fk_za_users_role FOREIGN KEY (role_id) REFERENCES za_roles(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_user_group_members (
    user_id  VARCHAR(36) NOT NULL,
    group_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (user_id, group_id),
    CONSTRAINT fk_ugm_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE,
    CONSTRAINT fk_ugm_group FOREIGN KEY (group_id) REFERENCES za_user_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL,
    jwt_id      VARCHAR(36)  NOT NULL UNIQUE,
    ip_address  VARCHAR(64)  NOT NULL DEFAULT '',
    user_agent  VARCHAR(255) NOT NULL DEFAULT '',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME     NOT NULL,
    revoked_at  DATETIME,
    KEY ix_za_sessions_user_id (user_id),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_authorizations (
    id              VARCHAR(36)  PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    user_id         VARCHAR(36),
    user_group_id   VARCHAR(36),
    host_id         VARCHAR(36),
    host_group_id   VARCHAR(36),
    credential_id   VARCHAR(36),
    actions         JSON         NOT NULL,
    date_start      DATETIME,
    date_expired    DATETIME,
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_za_authz_user_id (user_id),
    KEY ix_za_authz_user_group_id (user_group_id),
    KEY ix_za_authz_host_id (host_id),
    KEY ix_za_authz_host_group_id (host_group_id),
    CONSTRAINT fk_authz_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE,
    CONSTRAINT fk_authz_group FOREIGN KEY (user_group_id) REFERENCES za_user_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_command_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL,
    patterns    JSON         NOT NULL,
    match_type  VARCHAR(20)  NOT NULL DEFAULT 'regex',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_command_filters (
    id                VARCHAR(36)  PRIMARY KEY,
    name              VARCHAR(200) NOT NULL,
    command_group_id  VARCHAR(36)  NOT NULL,
    user_id           VARCHAR(36),
    user_group_id     VARCHAR(36),
    host_id           VARCHAR(36),
    host_group_id     VARCHAR(36),
    action            VARCHAR(20)  NOT NULL DEFAULT 'deny',
    priority          INT          NOT NULL DEFAULT 50,
    enabled           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_za_cmdfilter_group (command_group_id),
    CONSTRAINT fk_cmdfilter_group FOREIGN KEY (command_group_id) REFERENCES za_command_groups(id) ON DELETE CASCADE,
    CONSTRAINT fk_cmdfilter_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE,
    CONSTRAINT fk_cmdfilter_group2 FOREIGN KEY (user_group_id) REFERENCES za_user_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_login_acls (
    id            VARCHAR(36)  PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    user_id       VARCHAR(36),
    user_group_id VARCHAR(36),
    ip_cidr       VARCHAR(64),
    time_start    VARCHAR(5),
    time_end      VARCHAR(5),
    days_of_week  JSON         NOT NULL,
    action        VARCHAR(10)  NOT NULL DEFAULT 'allow',
    priority      INT          NOT NULL DEFAULT 50,
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_loginacl_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE,
    CONSTRAINT fk_loginacl_group FOREIGN KEY (user_group_id) REFERENCES za_user_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_api_tokens (
    id           VARCHAR(36)  PRIMARY KEY,
    user_id      VARCHAR(36)  NOT NULL,
    name         VARCHAR(200) NOT NULL,
    token_hash   VARCHAR(255) NOT NULL,
    token_prefix VARCHAR(12)  NOT NULL,
    scopes       JSON         NOT NULL,
    expires_at   DATETIME,
    last_used_at DATETIME,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at   DATETIME,
    KEY ix_za_api_tokens_user_id (user_id),
    KEY ix_za_api_tokens_prefix (token_prefix),
    CONSTRAINT fk_apitoken_user FOREIGN KEY (user_id) REFERENCES za_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_audit_logs (
    id            VARCHAR(36)  PRIMARY KEY,
    user_id       VARCHAR(36),
    username      VARCHAR(100) NOT NULL DEFAULT '',
    action        VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50)  NOT NULL DEFAULT '',
    resource_id   VARCHAR(36)  NOT NULL DEFAULT '',
    details       JSON         NOT NULL,
    ip_address    VARCHAR(64)  NOT NULL DEFAULT '',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_za_audit_created_at (created_at),
    KEY ix_za_audit_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO za_roles (id, name, description, permissions) VALUES
    ('00000000-0000-0000-0000-000000000001', 'superadmin', 'Full access, including Settings/Integrations', '["*"]'),
    ('00000000-0000-0000-0000-000000000002', 'admin', 'Manage assets, credentials, automation', '["assets:*","credentials:*","automation:*"]'),
    ('00000000-0000-0000-0000-000000000003', 'user', 'Workbench access to authorized hosts only', '["workbench:use"]');

-- ============================================================================
-- INVENTORY DB TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS za_zones (
    id             VARCHAR(36)  PRIMARY KEY,
    name           VARCHAR(200) NOT NULL UNIQUE,
    description    TEXT         NOT NULL,
    parent_zone_id VARCHAR(36),
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_zone_parent FOREIGN KEY (parent_zone_id) REFERENCES za_zones(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_gateways (
    id            VARCHAR(36)  PRIMARY KEY,
    zone_id       VARCHAR(36),
    name          VARCHAR(200) NOT NULL,
    host          VARCHAR(255) NOT NULL,
    port          INT          NOT NULL DEFAULT 22,
    username      VARCHAR(100) NOT NULL DEFAULT '',
    credential_id VARCHAR(36),
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_gateway_zone FOREIGN KEY (zone_id) REFERENCES za_zones(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_host_groups (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(200) NOT NULL UNIQUE,
    description TEXT         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_hosts (
    id              VARCHAR(36)  PRIMARY KEY,
    zabbix_hostid   VARCHAR(50)  UNIQUE,
    name            VARCHAR(200) NOT NULL,
    ip              VARCHAR(100) NOT NULL,
    port            INT          NOT NULL DEFAULT 22,
    os_type         VARCHAR(20)  NOT NULL DEFAULT 'linux',
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    zone_id         VARCHAR(36),
    last_synced_at  DATETIME,
    is_reachable    BOOLEAN,
    last_ping_at    DATETIME,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_host_zone FOREIGN KEY (zone_id) REFERENCES za_zones(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_host_group_members (
    host_id  VARCHAR(36) NOT NULL,
    group_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (host_id, group_id),
    CONSTRAINT fk_hgm_host FOREIGN KEY (host_id) REFERENCES za_hosts(id) ON DELETE CASCADE,
    CONSTRAINT fk_hgm_group FOREIGN KEY (group_id) REFERENCES za_host_groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_credential_templates (
    id               VARCHAR(36)  PRIMARY KEY,
    name             VARCHAR(200) NOT NULL UNIQUE,
    secret_type      VARCHAR(20)  NOT NULL DEFAULT 'password',
    description      TEXT         NOT NULL,
    default_username VARCHAR(100) NOT NULL DEFAULT '',
    default_params   JSON         NOT NULL,
    push_enabled     BOOLEAN      NOT NULL DEFAULT FALSE,
    rotation_days    INT,
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_credentials (
    id                VARCHAR(36)  PRIMARY KEY,
    name              VARCHAR(200) NOT NULL DEFAULT '',
    template_id       VARCHAR(36),
    username          VARCHAR(100) NOT NULL,
    secret_type       VARCHAR(20)  NOT NULL DEFAULT 'password',
    secret_ciphertext TEXT         NOT NULL,
    credential_scope  VARCHAR(20)  NOT NULL DEFAULT 'host',
    is_default        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_cred_template FOREIGN KEY (template_id) REFERENCES za_credential_templates(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS za_credential_host_links (
    credential_id VARCHAR(36) NOT NULL,
    host_id       VARCHAR(36) NOT NULL,
    PRIMARY KEY (credential_id, host_id),
    CONSTRAINT fk_chl_cred FOREIGN KEY (credential_id) REFERENCES za_credentials(id) ON DELETE CASCADE,
    CONSTRAINT fk_chl_host FOREIGN KEY (host_id) REFERENCES za_hosts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
