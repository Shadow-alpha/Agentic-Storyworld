from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .main import AppConfig, build_app, load_app_config


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


class GoalRequest(BaseModel):
    goal_id: str


class PlayerCustomizationRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


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

    def resolve_core_app(game_id: str | None = None):
        effective_game_id = game_id or settings.game_id
        return build_app(settings.with_game_id(effective_game_id))

    @app.get("/api/health")
    async def health(game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        return {
            "status": "ok",
            "game_id": core_app.game_id,
        }

    @app.get("/api/games")
    async def get_games() -> dict[str, Any]:
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
    async def get_game_state(game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        return core_app.get_ui_state()

    @app.get("/api/game/saves")
    async def get_game_saves(game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        return {
            "game_id": core_app.game_id,
            "saves": core_app.state_manager.list_saves(),
        }

    @app.post("/api/game/message")
    async def post_game_message(request: MessageRequest, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        try:
            return core_app.process_turn(request.user_input.model_dump())
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/message/stream")
    async def post_game_message_stream(request: MessageRequest, game_id: str | None = None) -> StreamingResponse:
        core_app = resolve_core_app(game_id)
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
    async def post_game_reset(game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        try:
            return core_app.reset_game()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/player_customization")
    async def post_player_customization(
        request: PlayerCustomizationRequest,
        game_id: str | None = None,
    ) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        try:
            return core_app.customize_player(request.values)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/save")
    async def post_game_save(request: SlotRequest, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        slot_id = request.slot_id.strip()
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id must not be empty.")
        try:
            return core_app.save_game(slot_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/load")
    async def post_game_load(request: SlotRequest, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        slot_id = request.slot_id.strip()
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id must not be empty.")
        try:
            return core_app.load_game(slot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/goals/activate")
    async def post_goal_activate(request: GoalRequest, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        try:
            return core_app.activate_goal(request.goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/game/goals/deactivate")
    async def post_goal_deactivate(request: GoalRequest, game_id: str | None = None) -> dict[str, Any]:
        core_app = resolve_core_app(game_id)
        try:
            return core_app.deactivate_goal(request.goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn
    settings = load_app_config()
    host = "127.0.0.1"
    port = 8000
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
