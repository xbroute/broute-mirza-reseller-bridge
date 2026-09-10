from __future__ import annotations

from pathlib import Path


def test_installer_keeps_secrets_root_owned_but_readable_by_service_group():
    text = Path("install.sh").read_text(encoding="utf-8")

    assert 'chown root:broute-bridge "$ETC"' in text
    assert 'chmod 0750 "$ETC"' in text
    assert 'chown root:broute-bridge "$ETC/master.key"' in text
    assert 'chmod 0640 "$ETC/master.key"' in text
    assert 'chown root:broute-bridge "$ETC/bridge.env"' in text
    assert 'chmod 0640 "$ETC/bridge.env"' in text
    assert 'chown -R broute-bridge:broute-bridge "$STATE"' in text


def test_installer_probes_real_venv_and_dependency_consistency():
    text = Path("install.sh").read_text(encoding="utf-8")

    assert 'python3 -m venv "$probe/venv"' in text
    assert 'apt-get install -y python3-venv' in text
    assert '"$RELEASE/.venv/bin/pip" check' in text


def test_bridge_stays_bound_to_loopback_by_default():
    install = Path("install.sh").read_text(encoding="utf-8")
    service = Path("deploy/systemd/broute-bridge.service").read_text(encoding="utf-8")

    assert "BROUTE_BIND_HOST=127.0.0.1" in install
    assert "ReadWritePaths=/var/lib/broute-bridge" in service
    assert "ReadOnlyPaths=/etc/broute-bridge" in service


def test_production_installers_default_to_stable_and_use_exit_guarded_transactions():
    install = Path("install.sh").read_text(encoding="utf-8")
    setup = Path("scripts/setup-wizard.sh").read_text(encoding="utf-8")
    mirza = Path("scripts/install-mirza-compat.sh").read_text(encoding="utf-8")

    assert 'REF="${BROUTE_REF:-stable}"' in install
    assert 'REF="${BROUTE_REF:-stable}"' in mirza
    assert "trap on_exit EXIT" in install
    assert "trap on_exit EXIT" in setup
    assert "/stable/scripts/install-mirza-compat.sh" in setup


def test_automatic_rollback_normalizes_restored_database_permissions():
    install = Path("install.sh").read_text(encoding="utf-8")

    assert "normalize_runtime_permissions" in install
    assert 'chmod 0600 "$STATE/bridge.db"' in install
    assert 'systemctl disable broute-bridge' in install
