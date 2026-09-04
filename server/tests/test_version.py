"""De versiestempel van de site: wat hij zegt en wat hij nooit verzint."""
import re

import pytest

from app import auth, routes_api, templating, version


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def test_versie_is_semantisch():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.VERSION)


def test_ingebakken_build_uit_de_omgeving(monkeypatch):
    monkeypatch.setenv("MM_BUILD_SHA", "ABC1234")
    monkeypatch.setenv("MM_BUILD_DATE", "2026-09-04")
    i = version.info()
    assert i["sha"] == "abc1234" and i["date"] == "2026-09-04"
    assert i["label"] == "v%s · abc1234 · 2026-09-04" % version.VERSION


def test_zonder_build_info_geen_verzonnen_waarde(monkeypatch):
    """Geen omgeving en geen git: dan staat er 'dev', niet iets wat op een
    commit lijkt. Een datum die geen datum is, verdwijnt in plaats van mee te
    reizen naar de HTML."""
    monkeypatch.delenv("MM_BUILD_SHA", raising=False)
    monkeypatch.setenv("MM_BUILD_DATE", "gisteren")
    monkeypatch.setattr(version, "_git_short", lambda: "")
    i = version.info()
    assert i["sha"] == "dev" and i["date"] == ""
    assert i["label"] == "v%s · dev" % version.VERSION


def test_rommel_in_de_omgeving_wordt_niet_getoond(monkeypatch):
    monkeypatch.setenv("MM_BUILD_SHA", "<script>alert(1)</script>")
    monkeypatch.setattr(version, "_git_short", lambda: "")
    assert version.info()["sha"] == "dev"


def test_footer_kent_de_build():
    """Elke template ziet dezelfde stempel via een Jinja-global; de footer
    hoeft hem niet uit elke route aangereikt te krijgen."""
    build = templating.templates.env.globals["build"]
    assert build["version"] == version.VERSION and build["label"].startswith("v")


def test_ping_meldt_de_versie(db):
    token = auth.create_token("test")
    antwoord = routes_api.ping("Bearer " + token)
    assert antwoord["app_version"] == version.VERSION
    assert antwoord["build"]
