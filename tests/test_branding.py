"""What an instance is called, and how it looks.

There is no customization: an instance looks like LLeMbas. What is left to pin
is that the **name** is a General setting that reaches every page and the model,
that saving it drops the snapshot, that the themes are the two built-ins, and
that the pages which made an instance somebody else's are gone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lembas.services import branding, settings_store


@pytest.fixture(autouse=True)
def clean(db):
    branding.forget()
    yield
    branding.forget()


def _save_name(client: TestClient, name: str):
    return client.post(
        "/admin/general", data={"instance_name": name}, follow_redirects=False
    )


def test_the_name_reaches_the_title_and_the_model(db, client, registered):
    from lembas.services import harness

    assert _save_name(client, "Rivendell").status_code == 303

    assert "<title>New chat - Rivendell</title>" in client.get("/chat").text
    assert harness.context_variables(db, None, [])["instance_name"] == "Rivendell"


def test_a_save_drops_the_snapshot(db, client, registered):
    assert branding.for_db(db).name == branding.DEFAULT_NAME
    _save_name(client, "Rivendell")
    assert branding.for_db(db).name == "Rivendell"


def test_an_empty_name_is_lembas(db, client, registered):
    _save_name(client, "   ")
    assert settings_store.get_group(db, settings_store.GENERAL)["instance_name"] == ""
    assert branding.for_db(db).name == "LLeMbas"


def test_the_name_is_on_general(db, client, registered):
    assert 'name="instance_name"' in client.get("/admin/general").text


def test_the_themes_are_the_two_built_ins(db):
    assert [theme.id for theme in branding.build().themes] == ["moria", "shire"]


@pytest.mark.parametrize("path", ["/admin/customization", "/branding.css"])
def test_customization_is_gone(client, registered, path):
    assert client.get(path).status_code == 404


def test_the_doors_of_durin_are_a_riddle_not_a_greeting():
    """"Speak, friend, and enter" invites a friend to speak. The inscription is a
    riddle whose answer is to say the word *friend*, so the shipped line has no
    commas, and they must not come back in a tidy-up."""
    from lembas.services.branding import FLAVOUR

    for key in ("chat_empty", "error_403"):
        line = FLAVOUR[key][2]
        assert line.startswith("Speak friend and enter.")
        assert "Speak, friend" not in line
