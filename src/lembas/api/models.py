"""What the model menu asks for when it opens."""

from __future__ import annotations

from fastapi import APIRouter

from lembas.api.deps import Db, RequiredUser
from lembas.services import chat as chat_service
from lembas.services import model_state

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/state")
async def model_states(db: Db, user: RequiredUser) -> dict:
    """`{"states": {model_id: "loaded" | "loading" | "unloaded"}}`.

    Only models this reader may use, so the answer never names a model the
    menu would not show. Only those whose endpoint reports a state, so a hosted
    API's models are simply absent. See `services/model_state.py`.
    """
    models = chat_service.available_models(db, user)
    return {"states": await model_state.states_for(models)}
