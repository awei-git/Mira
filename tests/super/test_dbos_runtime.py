from __future__ import annotations


def test_dbos_runtime_uses_control_database_for_system_and_app(monkeypatch):
    import runtime.dbos_runtime as dbos_runtime

    captured = {}

    class FakeDBOS:
        def __init__(self, *, config):
            captured["config"] = config
            self.launched = False

        def launch(self):
            self.launched = True
            captured["launched"] = True

        def destroy(self):
            captured["destroyed"] = True

    monkeypatch.setattr(dbos_runtime, "_dbos", None)
    monkeypatch.setattr(dbos_runtime, "DBOS", FakeDBOS)
    monkeypatch.setattr(dbos_runtime, "CONTROL_DATABASE_URL", "postgresql://localhost/mira")
    monkeypatch.setattr(dbos_runtime, "DBOS_SYSTEM_SCHEMA", "mira_dbos")
    monkeypatch.setattr(dbos_runtime, "DBOS_APPLICATION_VERSION", "test-version")
    monkeypatch.setattr(dbos_runtime, "DBOS_RUN_ADMIN_SERVER", False)

    instance = dbos_runtime.get_dbos()

    assert instance.launched is True
    assert captured["config"]["system_database_url"] == "postgresql://localhost/mira"
    assert captured["config"]["application_database_url"] == "postgresql://localhost/mira"
    assert captured["config"]["dbos_system_schema"] == "mira_dbos"
    assert captured["config"]["application_version"] == "test-version"
    assert captured["config"]["run_admin_server"] is False

    dbos_runtime.destroy_dbos()
    assert captured["destroyed"] is True
