from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .main import AppConfig, build_app, load_app_config


mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")


class UserInputPayload(BaseModel):
    input_mode: str
    raw_text: str | None = None
    choice_id: str | None = None
    selected_choice: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class MessageRequest(BaseModel):
    user_input: UserInputPayload


class SlotRequest(BaseModel):
    slot_id: str


class RenameSlotRequest(BaseModel):
    old_slot_id: str
    new_slot_id: str


class PlayerCustomizationRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class AccessLoginRequest(BaseModel):
    invite_code: str


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: AppConfig | None = None) -> FastAPI:
    settings = settings or load_app_config()
    project_root = Path(__file__).resolve().parent.parent
    web_root = project_root / "web"
    frontend_dist_root = project_root / "frontend" / "dist"
    games_dir = settings.games_dir
    active_frontend_root = frontend_dist_root if frontend_dist_root.exists() else web_root

    app = FastAPI(title="Character Interactive System Demo")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if web_root.exists():
        app.mount("/web", StaticFiles(directory=web_root), name="web")
    if (active_frontend_root / "assets").exists():
        app.mount("/assets", StaticFiles(directory=active_frontend_root / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(active_frontend_root / "index.html")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(active_frontend_root / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/game/image/{filename}")
    async def get_game_image(filename: str, game_id: str | None = None) -> FileResponse:
        safe_name = Path(filename).name
        if not re.search(r"\.(png|jpg|jpeg|webp|gif|svg)$", safe_name, flags=re.IGNORECASE):
            raise HTTPException(status_code=404, detail="Image not found.")
        image_path = games_dir / (game_id or settings.game_id) / "images" / safe_name
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail="Image not found.")
        return FileResponse(image_path)

    def access_enabled() -> bool:
        return bool(settings.access.get("enabled", False))

    def _safe_user_id(invite_code: str) -> str:
        user_id = re.sub(r"[^A-Za-z0-9_-]+", "_", invite_code.strip())
        return user_id.strip("_") or "root"

    def _invite_codes() -> set[str]:
        codes = settings.access.get("invite_codes", ["root"])
        if not isinstance(codes, list):
            return {"root"}
        return {str(code).strip() for code in codes if str(code).strip()}

    def _token_secret() -> bytes:
        secret = str(settings.access.get("token_secret") or "local-dev-secret")
        return secret.encode("utf-8")

    def _b64_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _b64_decode(data: str) -> bytes:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    def issue_token(user_id: str) -> str:
        payload = _b64_encode(json.dumps({"user_id": user_id}, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(_token_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        return f"{payload}.{_b64_encode(signature)}"

    def verify_token(token: str) -> str | None:
        try:
            payload, signature = token.split(".", 1)
            expected = _b64_encode(hmac.new(_token_secret(), payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            data = json.loads(_b64_decode(payload).decode("utf-8"))
        except Exception:
            return None
        user_id = str(data.get("user_id") or "").strip()
        return user_id or None

    def require_user_id(request: Request) -> str:
        if not access_enabled():
            return "root"
        authorization = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            raise HTTPException(status_code=401, detail="Invite code required.")
        user_id = verify_token(authorization[len(prefix) :].strip())
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid invite token.")
        return user_id

    def resolve_core_app(game_id: str | None = None, user_id: str = "root"):
        effective_game_id = game_id or settings.game_id
        return build_app(settings.with_game_id(effective_game_id).with_user_id(user_id))

    @app.get("/api/access/status")
    async def get_access_status() -> dict[str, Any]:
        return {"enabled": access_enabled()}

    @app.post("/api/access/login")
    async def post_access_login(request: AccessLoginRequest) -> dict[str, Any]:
        invite_code = request.invite_code.strip()
        if access_enabled() and invite_code not in _invite_codes():
            raise HTTPException(status_code=401, detail="Invalid invite code.")
        user_id = _safe_user_id(invite_code or "root")
        return {"access_token": issue_token(user_id), "user_id": user_id}

    @app.get("/api/health")
    async def health(game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        return {
            "status": "ok",
            "game_id": core_app.game_id,
        }

    @app.get("/api/games")
    async def get_games(request: Request) -> dict[str, Any]:
        require_user_id(request)
        games = []
        if games_dir.exists():
            for child_dir in sorted(path for path in games_dir.iterdir() if path.is_dir()):
                config_path = child_dir / "base" / "config.json"
                config = {}
                if config_path.exists():
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                games.append(
                    {
                        "game_id": child_dir.name,
                        "title": config.get("title", child_dir.name),
                    }
                )
        return {
            "default_game_id": settings.game_id,
            "games": games,
        }

    @app.get("/api/game/state")
    async def get_game_state(request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(request))
        return core_app.get_ui_state()

    @app.get("/api/game/saves")
    async def get_game_saves(request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(request))
        return {
            "game_id": core_app.game_id,
            "saves": core_app.state_manager.list_saves(),
        }

    @app.post("/api/game/message")
    async def post_game_message(request: MessageRequest, http_request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        try:
            return core_app.process_turn(request.user_input.model_dump())
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/message/stream")
    async def post_game_message_stream(request: MessageRequest, http_request: Request, game_id: str | None = None) -> StreamingResponse:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        user_input = request.user_input.model_dump()

        def event_generator():
            try:
                for item in core_app.stream_turn(user_input):
                    yield _format_sse(item["event"], item["data"])
            except FileNotFoundError as exc:
                yield _format_sse("error", {"detail": str(exc)})
            except ValueError as exc:
                yield _format_sse("error", {"detail": str(exc)})
            except Exception as exc:
                yield _format_sse("error", {"detail": str(exc)})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/game/reset")
    async def post_game_reset(request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(request))
        try:
            return core_app.reset_game()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/player_customization")
    async def post_player_customization(
        request: PlayerCustomizationRequest,
        http_request: Request,
        game_id: str | None = None,
    ) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        try:
            return core_app.customize_player(request.values)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/save")
    async def post_game_save(request: SlotRequest, http_request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        slot_id = request.slot_id.strip()
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id must not be empty.")
        try:
            return core_app.save_game(slot_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/load")
    async def post_game_load(request: SlotRequest, http_request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        slot_id = request.slot_id.strip()
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id must not be empty.")
        try:
            return core_app.load_game(slot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/save/delete")
    async def post_game_delete_save(request: SlotRequest, http_request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        slot_id = request.slot_id.strip()
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id must not be empty.")
        try:
            return core_app.delete_save(slot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/save/rename")
    async def post_game_rename_save(request: RenameSlotRequest, http_request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(http_request))
        old_slot_id = request.old_slot_id.strip()
        new_slot_id = request.new_slot_id.strip()
        if not old_slot_id or not new_slot_id:
            raise HTTPException(status_code=400, detail="slot ids must not be empty.")
        try:
            return core_app.rename_save(old_slot_id, new_slot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/turns/revert_latest")
    async def post_revert_latest_turn(request: Request, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id, require_user_id(request))
        try:
            return core_app.revert_latest_turn()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn
    settings = load_app_config()
    host = settings.host
    port = settings.port
    print(f"App server running at http://{host}:{port} for game '{settings.game_id}'")
    uvicorn.run(
        "src.app_server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info" if settings.debug else "warning",
    )


if __name__ == "__main__":
    main()
