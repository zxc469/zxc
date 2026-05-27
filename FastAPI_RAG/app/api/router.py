from fastapi import APIRouter

from app.api.auth_api import auth_router
from app.api.chat_api import chat_router
from app.api.files_api import files_router
from app.api.ws_api import ws_router
from app.api.session_routes import agent_session_router, user_session_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(chat_router)
api_router.include_router(files_router)
api_router.include_router(ws_router)
api_router.include_router(user_session_router)
api_router.include_router(agent_session_router)
