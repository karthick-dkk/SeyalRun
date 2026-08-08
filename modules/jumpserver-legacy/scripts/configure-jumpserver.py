#!/usr/bin/env python3
"""
configure-jumpserver.py
Configures JumpServer LDAP auth and OAuth2 provider via REST API.
Run from Mac: python3 scripts/configure-jumpserver.py
"""

import json
import ssl
import sys
import urllib.error
import urllib.request

BASE = "http://192.168.64.2"  # HTTP — JumpServer is on port 80 internally
ADMIN_USER = "admin"
ADMIN_PASS = "test@123"

LDAP_CONFIG = {
    "AUTH_LDAP": True,
    "AUTH_LDAP_SERVER_URI": "ldap://127.0.0.1:389",
    "AUTH_LDAP_BIND_DN": "cn=svc-jumpserver,ou=users,dc=lab,dc=pravesh,dc=local",
    "AUTH_LDAP_BIND_PASSWORD": "CHANGE_ME_LDAP_BIND_PASSWORD",  # pragma: allowlist secret
    "AUTH_LDAP_SEARCH_OU": "ou=users,dc=lab,dc=pravesh,dc=local",
    "AUTH_LDAP_SEARCH_FILTER": "(uid=%(user)s)",
    "AUTH_LDAP_USER_ATTR_MAP": {
        "name": "cn",
        "email": "mail",
        "username": "uid",
    },
    "AUTH_LDAP_GROUP_SEARCH_OU": "ou=groups,dc=lab,dc=pravesh,dc=local",
    "AUTH_LDAP_GROUP_FILTER": "(objectClass=groupOfUniqueNames)",
    "AUTH_LDAP_GROUP_MEMBERSHIP_ATTR": "uniqueMember",
    "AUTH_LDAP_USER_LOGIN_ONLY_IN_USERS_GROUPS": True,
    "AUTH_LDAP_SYNC_IS_PERIODIC": True,
    "AUTH_LDAP_SYNC_INTERVAL": 24,
    "AUTH_LDAP_SYNC_ORG_ID": None,
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def api(method: str, path: str, data=None, token: str = "") -> dict:
    url = f"{BASE}/api/v1{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read()) if e.fp else {"error": str(e)}


def get_token() -> str:
    r = api(
        "POST",
        "/authentication/auth/",
        {"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    token = r.get("token", "")
    if not token:
        print(f"ERROR: Auth failed: {r}")
        sys.exit(1)
    print(f"✓ Authenticated as {ADMIN_USER}")
    return token


def configure_ldap(token: str) -> None:
    print("\n── Step 1: Configure LDAP authentication ──")
    r = api("PATCH", "/settings/setting/", LDAP_CONFIG, token)
    if "error" in r or "detail" in r:
        print(f"  WARNING: {r}")
    else:
        print("  ✓ LDAP settings saved")

    # Test LDAP connectivity
    print("  Testing LDAP connection...")
    r = api(
        "POST",
        "/settings/ldap/testing/",
        {
            "AUTH_LDAP_SERVER_URI": LDAP_CONFIG["AUTH_LDAP_SERVER_URI"],
            "AUTH_LDAP_BIND_DN": LDAP_CONFIG["AUTH_LDAP_BIND_DN"],
            "AUTH_LDAP_BIND_PASSWORD": LDAP_CONFIG["AUTH_LDAP_BIND_PASSWORD"],
            "AUTH_LDAP_SEARCH_OU": LDAP_CONFIG["AUTH_LDAP_SEARCH_OU"],
            "AUTH_LDAP_SEARCH_FILTER": LDAP_CONFIG["AUTH_LDAP_SEARCH_FILTER"],
            "AUTH_LDAP_USER_ATTR_MAP": LDAP_CONFIG["AUTH_LDAP_USER_ATTR_MAP"],
        },
        token,
    )
    print(f"  LDAP test result: {r}")


def configure_oauth2(token: str) -> None:
    print("\n── Step 2: Create OAuth2 application for Zabbix SSO ──")

    # Check existing apps
    existing = api("GET", "/authentication/applications/", token=token)
    if isinstance(existing, list):
        for app in existing:
            if app.get("name") == "Zabbix-SSO":
                print(
                    f"  ✓ OAuth2 app 'Zabbix-SSO' already exists (client_id: {app.get('client_id', '')})"
                )
                return

    r = api(
        "POST",
        "/authentication/applications/",
        {
            "name": "Zabbix-SSO",
            "client_type": "confidential",
            "authorization_grant_type": "authorization-code",
            "redirect_uris": "https://192.168.64.8/index.php https://192.168.64.8/",
            "skip_authorization": True,
            "algorithm": "RS256",
        },
        token,
    )

    if "client_id" in r:
        print("  ✓ OAuth2 app created")
        print(f"    client_id:     {r['client_id']}")
        print(f"    client_secret: {r.get('client_secret', 'see JumpServer UI')}")
        print("    authorize_url: http://192.168.64.2/o/authorize/")
        print("    token_url:     http://192.168.64.2/o/token/")
    else:
        print(f"  INFO: {r}")


def sync_ldap_users(token: str) -> None:
    print("\n── Step 3: Trigger LDAP user sync ──")
    r = api("POST", "/users/users/sync_ldap_users/", {}, token)
    print(f"  Sync result: {r}")


def create_system_user(token: str) -> None:
    print("\n── Step 4: Create system user for Linux domain push ──")

    existing = api("GET", "/assets/system-users/?name=pravesh-admin", token=token)
    if isinstance(existing, dict) and existing.get("count", 0) > 0:
        su = existing["results"][0]
        print(f"  ✓ System user 'pravesh-admin' already exists (id: {su['id']})")
        return

    r = api(
        "POST",
        "/assets/system-users/",
        {
            "name": "pravesh-admin",
            "username": "pravesh-admin",
            "login_mode": "auto",
            "auth_method": "ssh-key",
            "auto_push": True,
            "sudo": "ALL=(ALL) NOPASSWD: ALL",
            "shell": "/bin/bash",
            "comment": "SeyalRun managed system user — pushed to all Linux hosts",
        },
        token,
    )

    if "id" in r:
        print(f"  ✓ System user created (id: {r['id']})")
    else:
        print(f"  INFO: {r}")


def verify(token: str) -> None:
    print("\n── Verification ──")

    # Check LDAP setting saved
    s = api("GET", "/settings/setting/", token=token)
    ldap_on = s.get("AUTH_LDAP", False) if isinstance(s, dict) else False
    print(f"  AUTH_LDAP enabled:     {'✓' if ldap_on else '✗'} ({ldap_on})")

    # Check OAuth2 apps
    apps = api("GET", "/authentication/applications/", token=token)
    app_names = [a.get("name") for a in apps] if isinstance(apps, list) else []
    print(f"  OAuth2 apps:           {'✓' if 'Zabbix-SSO' in app_names else '✗'} {app_names}")

    # Check system users
    su = api("GET", "/assets/system-users/?name=pravesh-admin", token=token)
    su_count = su.get("count", 0) if isinstance(su, dict) else 0
    print(f"  System user created:   {'✓' if su_count > 0 else '✗'}")

    print("\n  OAuth2 endpoints:")
    print("    Authorize: http://192.168.64.2/o/authorize/")
    print("    Token:     http://192.168.64.2/o/token/")
    print("    UserInfo:  http://192.168.64.2/o/userinfo/")
    print("    JWKS:      http://192.168.64.2/o/.well-known/jwks.json")


if __name__ == "__main__":
    print("=== JumpServer Feature 1: SSO + LDAP Configuration ===\n")
    token = get_token()
    configure_ldap(token)
    configure_oauth2(token)
    sync_ldap_users(token)
    create_system_user(token)
    verify(token)
    print("\nDone. Run verification test:")
    print(
        "  curl -s http://192.168.64.2/api/v1/settings/setting/ -H 'Authorization: Bearer <token>' | python3 -c \"import sys,json; d=json.load(sys.stdin); print('LDAP:', d.get('AUTH_LDAP'))\""
    )
