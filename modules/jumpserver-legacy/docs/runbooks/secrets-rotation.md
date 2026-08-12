# Runbook: Secrets Rotation
**Severity:** P2 (planned), P1 (emergency — suspected compromise)
**Owner:** karthick (karthidkk123@gmail.com)
**Last tested:** Never (TEST THIS IN LAB)

## Rotation Schedule

| Secret | Rotation interval | Method |
|---|---|---|
| JumpServer SECRET_KEY | 90 days or on compromise | Ansible playbook |
| DB_PASSWORD (PostgreSQL) | 90 days | Ansible playbook + DB user update |
| REDIS_PASSWORD | 90 days | Ansible + JumpServer config update |
| FreeIPA svc-jumpserver password | 90 days | IPA command |
| JWT signing key (launch-token) | 90 days | Key rotation with 24h grace |
| Zabbix API token | 30 days | Zabbix UI |
| mTLS certificates (future) | 90 days | Vault PKI auto-renew |

---

## 1. Rotate JumpServer SECRET_KEY + DB + Redis Passwords

**Impact:** JumpServer must restart. All active sessions will be invalidated. Users will need to log in again.

```bash
# Use the Ansible playbook (automated, safe):
ansible-playbook -i infra/ansible/inventory/hosts.ini \
  infra/ansible/playbooks/jumpserver-config.yml \
  --ask-vault-pass
```

**If running manually:**
```bash
# On Server 1 (192.168.64.2)
ssh test@192.168.64.2

# Generate new secrets
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
NEW_DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
NEW_REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Update PostgreSQL password
echo "test" | sudo -S docker exec jms_postgresql \
  psql -U postgres -c "ALTER USER postgres PASSWORD '${NEW_DB_PASS}';"

# Update Redis password
echo "test" | sudo -S docker exec jms_redis \
  redis-cli CONFIG SET requirepass "${NEW_REDIS_PASS}"

# Update config.txt
sudo sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${NEW_SECRET}/" /opt/jumpserver/config/config.txt
sudo sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=${NEW_DB_PASS}/" /opt/jumpserver/config/config.txt
sudo sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${NEW_REDIS_PASS}/" /opt/jumpserver/config/config.txt

# Restrict permissions
sudo chmod 600 /opt/jumpserver/config/config.txt

# Restart JumpServer
echo "test" | sudo -S /opt/jumpserver-installer-v4.10.16/jmsctl.sh restart

# Verify JumpServer came back up
sleep 30
curl -o /dev/null -w "%{http_code}" http://192.168.64.2/api/v1/settings/public/
# Expect: 401
```

**After rotation:** Update Ansible vault.yml with new values:
```bash
ansible-vault edit infra/ansible/group_vars/all/vault.yml
```

---

## 2. Rotate FreeIPA Service Account Password

```bash
# On Server 1 — inside FreeIPA container
sudo docker exec -it freeipa bash
kinit admin  # enter IPA admin password
ipa user-mod svc-jumpserver --password  # enter new password
kdestroy
exit

# Update JumpServer LDAP bind password:
# JumpServer UI → System Settings → Authentication → LDAP
# Update "Bind Password" field
# Test LDAP connection before saving
```

---

## 3. Rotate JWT Signing Key (launch-token service)

Launch-token uses RS256 (asymmetric). Rotation requires a 24-hour grace period:

```bash
# Generate new RS256 key pair
openssl genrsa -out /tmp/jwt-new-private.pem 4096
openssl rsa -in /tmp/jwt-new-private.pem -pubout -out /tmp/jwt-new-public.pem

# Deploy new private key to service (keep old public key temporarily)
# 1. Update launch-token's CURRENT_PRIVATE_KEY env to new key
# 2. Add old public key to PREVIOUS_PUBLIC_KEY env (for 24h validation grace)
# 3. Wait 24 hours
# 4. Remove PREVIOUS_PUBLIC_KEY from env
# 5. Update JWKS endpoint returns only new public key

# Securely delete temp files
shred -u /tmp/jwt-new-private.pem /tmp/jwt-new-public.pem
```

---

## 4. Emergency Rotation (Suspected Compromise)

If you believe a secret has been leaked:

1. **Identify the compromised secret** — check git log, logs, chat history
2. **Revoke immediately** — don't wait for scheduled rotation
3. **Rotate all secrets at the same level** — if one was compromised, others in the same scope may be
4. **Check audit logs** — JumpServer session logs, SSSD logs for unauthorized access
5. **Rotate SECRET_KEY last** — rotating it first invalidates all existing sessions, making audit harder
6. **Document the incident** in `docs/incidents/YYYY-MM-DD-secret-compromise.md`

---

## Verification Checklist

After any rotation:
- [ ] JumpServer web UI is accessible (HTTP 401 on `/api/v1/settings/public/`)
- [ ] LDAP authentication works (try logging in with a FreeIPA user)
- [ ] Old DB password is rejected: `docker exec jms_postgresql psql -U postgres -c "\l"` with old password fails
- [ ] All sidecar services started successfully with new credentials
- [ ] Ansible vault.yml updated with new values
