"""What the apps read of a model and an agent chat on /api/v2 (2.0.0-beta.5):
a model's levels (harness spec 3.2.0) and its picture behind the token, and an
agent chat's session on its device."""

from __future__ import annotations

from sqlalchemy import select

from lembas.api.v2 import serialise
from lembas.db.models import ApiToken, Chat, Connection, Model, User
from lembas.security import api_tokens


def _model(db, model_id="m", **fields) -> Model:
    connection = db.scalar(select(Connection))
    if connection is None:
        connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
        db.add(connection)
        db.commit()
    values = {"capabilities_json": {"reasoning": True}, **fields}
    model = Model(connection_id=connection.id, model_id=model_id, **values)
    db.add(model)
    db.commit()
    return model


def _bearer(db, scopes=("app",)) -> dict[str, str]:
    user = db.scalars(select(User)).first()
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


def test_v2_models_carry_levels_and_a_token_readable_image(client, db, registered):
    from lembas.services import uploads

    uploads.models_dir().mkdir(parents=True, exist_ok=True)
    (uploads.models_dir() / "abc123.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _model(db, "qwen", reasoning_efforts=["medium"], thinking_switch=True, image_path="abc123.png")
    headers = _bearer(db)

    models = client.get("/api/v2/models", headers=headers).json()["models"]
    qwen = models[0]
    assert qwen["levels"] == ["off", "medium"] and qwen["control"] == "switch"
    assert qwen["image"] == "/api/v2/models/images/abc123.png"

    image = client.get(qwen["image"], headers=headers)
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    # The token's door only: no token, no picture; a name no model has, nothing.
    assert client.get(qwen["image"]).status_code in (401, 403)
    assert client.get("/api/v2/models/images/other.png", headers=headers).status_code == 404
    assert db.scalar(select(ApiToken)) is not None


def test_v2_chat_says_the_level_in_force_and_the_device_session():
    chat = Chat(id="c", user_id="u", kind="agent", model_id="m", params_json={})
    chat.device_token_id, chat.device_session_id = "t", "ses_1"

    shaped = serialise.chat(chat, level="medium")

    assert shaped["effort"] == "medium" and shaped["device_session_id"] == "ses_1"
    chat.device_token_id = None
    assert serialise.chat(chat)["device_session_id"] is None
