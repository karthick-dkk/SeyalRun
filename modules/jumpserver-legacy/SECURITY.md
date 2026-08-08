# Security Policy

## Supported Versions

| Component | Supported |
|---|---|
| JumpServer v4.10.x | Yes |
| FreeIPA (current) | Yes |
| Zabbix 7.0 LTS | Yes |
| Sidecar services (latest) | Yes |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: karthickdkk@outlook.com

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected component and version
- Potential impact

You will receive a response within 48 hours. If confirmed, a fix will be prioritized immediately.

## Secrets Handling

- No secrets, passwords, API keys, or certificates are ever committed to this repository
- Pre-commit hooks (gitleaks + detect-secrets) are enforced on all branches
- All secrets are managed via Ansible Vault (lab) or HashiCorp Vault (production)
- The `.secrets.baseline` file tracks known non-secret patterns to reduce false positives

## Known Security Constraints (Lab Environment)

- Server 1 (192.168.64.2) uses self-signed TLS certificates in the lab — expected
- SSH password authentication is disabled; key-based auth only
- JumpServer `SECRET_KEY` is rotated from the installer default before any data is entered
