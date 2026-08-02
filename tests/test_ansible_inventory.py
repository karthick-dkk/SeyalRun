"""P2 fix: the Ansible inventory must be built as structured YAML (yaml.safe_dump),
so a hostile host address / username / password can never inject inventory variables
or extra host entries the way hand-formatted INI text did.

PCI DSS Phase C: build_inventory() no longer places ansible_ssh_pass /
ansible_become_password / the raw SSH private key into the (plaintext-on-disk)
inventory dict at all — those go into vault_vars_by_host (written by the caller
as an ansible-vault-ENCRYPTED host_vars/<host>/vault.yml) or agent_keys (loaded
into a per-run ssh-agent), never a plaintext file. The injection-safety
invariants below still hold for whichever structure each secret now lives in.
"""

from __future__ import annotations

import yaml

import pytest


@pytest.fixture
def mod(load_module):
    return load_module(
        "services/automation-service/app/plugins/executors/ansible_playbook.py",
        "za_ansible_exec",
    )


def _build(mod, hosts, agent_sock="/tmp/fake-agent.sock"):
    inventory, vault_vars, agent_keys = mod.build_inventory(hosts, agent_sock)
    # Serialize + parse back exactly as the executor does before invoking ansible.
    inv = yaml.safe_load(yaml.safe_dump(inventory, default_flow_style=False, allow_unicode=True))
    return inv, vault_vars, agent_keys


def test_malicious_password_stays_a_single_value(mod):
    # A password crafted to inject a ProxyCommand var in the old INI format.
    evil = "hunter2 ansible_ssh_common_args='-o ProxyCommand=/bin/evil'"
    host = {"id": "h1", "ip": "10.0.0.5", "port": 22}
    cred = {"username": "root", "secret_type": "password", "secret": {"password": evil}}
    inv, vault_vars, _ = _build(mod, [(host, cred, None)])
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h1"]
    assert "ansible_ssh_pass" not in hv            # never in the plaintext inventory
    assert vault_vars["host_h1"]["ansible_ssh_pass"] == evil  # kept verbatim as ONE value
    assert "ansible_ssh_common_args" not in hv     # NOT injected as a separate var
    assert hv["ansible_host"] == "10.0.0.5"


def test_newline_in_address_cannot_inject_host_entries(mod):
    host = {"id": "h2", "ip": "1.2.3.4\n[targets]\nevil ansible_host=9.9.9.9", "port": 22}
    inv, _, _ = _build(mod, [(host, None, None)])
    hosts = inv["all"]["children"]["targets"]["hosts"]
    assert list(hosts.keys()) == ["host_h2"]        # exactly one host, no injected entry
    assert hosts["host_h2"]["ansible_host"] == host["ip"]  # newline preserved as data


def test_username_with_spaces_is_contained(mod):
    host = {"id": "h3", "ip": "10.0.0.9"}
    cred = {"username": "root x=y", "secret_type": "password", "secret": {"password": "p"}}
    inv, _, _ = _build(mod, [(host, cred, None)])
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h3"]
    assert hv["ansible_user"] == "root x=y"
    assert "x" not in hv  # the "x=y" fragment did not become its own var


def test_non_numeric_port_falls_back_to_22(mod):
    host = {"id": "h4", "ip": "10.0.0.4", "port": "not-a-port"}
    inv, _, _ = _build(mod, [(host, None, None)])
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h4"]
    assert hv["ansible_port"] == 22


def test_synthetic_key_decouples_name_from_address(mod):
    host = {"id": "abc", "ip": "10.0.0.1"}
    inv, _, _ = _build(mod, [(host, None, None)])
    hosts = inv["all"]["children"]["targets"]["hosts"]
    assert "host_abc" in hosts and hosts["host_abc"]["ansible_host"] == "10.0.0.1"


def test_sudo_password_lands_in_vault_vars_not_inventory(mod):
    # sudo_pw (become password) is only ever a value already resolved from the
    # credential vault, never a raw job-param string — same injection-safety
    # guarantee as the login password/username above, via the same structured
    # yaml.safe_dump path (not hand-formatted INI text) — and, as of Phase C,
    # never written to the plaintext inventory file at all.
    evil = "hunter2 ansible_become_method=su"
    host = {"id": "h5", "ip": "10.0.0.6"}
    inv, vault_vars, _ = _build(mod, [(host, None, evil)])
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h5"]
    assert hv["ansible_become"] is True
    assert "ansible_become_password" not in hv      # never in the plaintext inventory
    assert vault_vars["host_h5"]["ansible_become_password"] == evil  # kept verbatim as ONE value
    assert hv["ansible_become_method"] == "sudo"    # NOT overridden by the injected fragment


def test_no_sudo_omits_become_vars(mod):
    host = {"id": "h6", "ip": "10.0.0.7"}
    inv, vault_vars, _ = _build(mod, [(host, None, None)])
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h6"]
    assert "ansible_become" not in hv
    assert "ansible_become_password" not in hv
    assert "host_h6" not in vault_vars


def test_ssh_key_never_written_to_inventory_uses_agent_instead(mod):
    host = {"id": "h7", "ip": "10.0.0.8"}
    cred = {"username": "deploy", "secret_type": "ssh_key", "secret": {"private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----"}}
    inv, vault_vars, agent_keys = _build(mod, [(host, cred, None)], agent_sock="/tmp/agent-xyz.sock")
    hv = inv["all"]["children"]["targets"]["hosts"]["host_h7"]
    assert "ansible_ssh_private_key_file" not in hv
    assert "private_key" not in str(hv)              # the raw key text never lands in inventory
    assert "/tmp/agent-xyz.sock" in hv["ansible_ssh_common_args"]
    assert agent_keys == [("host_h7", cred["secret"]["private_key"])]
    assert "host_h7" not in vault_vars                # no password-type secret for this host


def test_password_and_ssh_key_hosts_can_coexist(mod):
    pw_host = {"id": "p1", "ip": "10.0.0.10"}
    pw_cred = {"username": "root", "secret_type": "password", "secret": {"password": "s3cret"}}
    key_host = {"id": "k1", "ip": "10.0.0.11"}
    key_cred = {"username": "deploy", "secret_type": "ssh_key", "secret": {"private_key": "PRIVATEKEY"}}
    inv, vault_vars, agent_keys = _build(mod, [(pw_host, pw_cred, None), (key_host, key_cred, None)])
    assert vault_vars["host_p1"]["ansible_ssh_pass"] == "s3cret"
    assert "host_k1" not in vault_vars
    assert agent_keys == [("host_k1", "PRIVATEKEY")]
    assert "ansible_ssh_common_args" not in inv["all"]["children"]["targets"]["hosts"]["host_p1"]
