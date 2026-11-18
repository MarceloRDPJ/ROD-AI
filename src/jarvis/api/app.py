"""ROD do Cerrado — API operacional do Raspberry Pi.

A API expõe somente capacidades verificadas no hardware atual: autenticação do
painel, saúde do serviço, telemetria do Pi, rede, hidratação, automações locais e
eventos. Integrações experimentais e hardware ausente não fazem parte deste
contrato.
"""

import hmac
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jarvis.api.dashboard_auth import DashboardAuth

logger = logging.getLogger("api")

app = FastAPI(title="ROD do Cerrado", version="1.0")
DASHBOARD_COOKIE = "rod_dashboard_session"
UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


class DashboardLogin(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def get_service(name: str):
    return getattr(app.state, name, None)


def service_required(name: str):
    service = get_service(name)
    if not service:
        raise HTTPException(status_code=503, detail=f"Service '{name}' not available")
    return service


def get_dashboard_auth() -> DashboardAuth:
    auth = get_service("dashboard_auth")
    if auth:
        return auth

    from jarvis.config import Config

    auth = DashboardAuth(
        enabled=Config.DASHBOARD_AUTH_ENABLED,
        username=Config.DASHBOARD_USERNAME,
        password_hash=Config.DASHBOARD_PASSWORD_HASH,
        session_secret=Config.DASHBOARD_SESSION_SECRET,
        session_ttl_seconds=Config.DASHBOARD_SESSION_TTL_SECONDS,
    )
    app.state.dashboard_auth = auth
    if auth.enabled and not auth.configured:
        logger.critical(
            "Dashboard/API bloqueado: configure DASHBOARD_PASSWORD_HASH e "
            "DASHBOARD_SESSION_SECRET"
        )
    return auth


def _dashboard_request_is_public(path: str, method: str) -> bool:
    del method
    return path in {
        "/login",
        "/api/auth/login",
        "/api/auth/status",
        "/api/system/health",
        "/favicon.ico",
    } or path.startswith("/static/")


@app.middleware("http")
async def protect_dashboard_api(request: Request, call_next):
    auth = get_dashboard_auth()
    path = request.url.path
    method = request.method.upper()

    if not auth.enabled or _dashboard_request_is_public(path, method):
        response = await call_next(request)
    elif not auth.configured:
        if path == "/":
            response = RedirectResponse("/login", status_code=307)
        else:
            response = JSONResponse(
                {"detail": "Dashboard authentication is enabled but not configured"},
                status_code=503,
            )
    else:
        session = auth.read_session(request.cookies.get(DASHBOARD_COOKIE))
        if session is None:
            if path == "/":
                response = RedirectResponse("/login", status_code=307)
            else:
                response = JSONResponse({"detail": "Authentication required"}, status_code=401)
        elif method in UNSAFE_HTTP_METHODS and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", "").encode("utf-8"),
            session.csrf_token.encode("ascii"),
        ):
            response = JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
        else:
            request.state.dashboard_session = session
            response = await call_next(request)

    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        "DashboardSession"
    ] = {
        "type": "apiKey",
        "in": "cookie",
        "name": DASHBOARD_COOKIE,
    }
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                if not _dashboard_request_is_public(path, method.upper()):
                    operation["security"] = [{"DashboardSession": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def serve_login():
    login_path = os.path.join(static_dir, "login.html")
    if os.path.exists(login_path):
        return FileResponse(login_path, media_type="text/html")
    return HTMLResponse("<h1>ROD</h1><p>Tela de login ausente.</p>", status_code=503)


@app.get("/api/auth/status")
async def dashboard_auth_status(request: Request):
    auth = get_dashboard_auth()
    session = (
        auth.read_session(request.cookies.get(DASHBOARD_COOKIE))
        if auth.configured
        else None
    )
    return {
        "enabled": auth.enabled,
        "configured": auth.configured,
        "authenticated": session is not None,
        "csrf_token": session.csrf_token if session else None,
        "expires_at": session.expires_at if session else None,
    }


@app.post("/api/auth/login")
async def dashboard_login(payload: DashboardLogin, request: Request):
    from jarvis.config import Config

    auth = get_dashboard_auth()
    if not auth.enabled:
        return {"status": "disabled"}
    if not auth.configured:
        raise HTTPException(status_code=503, detail="Dashboard authentication is not configured")

    client_key = request.client.host if request.client else "unknown"
    if not auth.login_allowed(client_key):
        raise HTTPException(status_code=429, detail="Muitas tentativas; aguarde alguns minutos")
    if not auth.credentials_match(payload.username, payload.password):
        auth.record_login_failure(client_key)
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")

    auth.clear_login_failures(client_key)
    token, session = auth.issue_session()
    response = JSONResponse(
        {
            "status": "ok",
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }
    )
    response.set_cookie(
        DASHBOARD_COOKIE,
        token,
        max_age=auth.session_ttl_seconds,
        httponly=True,
        secure=Config.DASHBOARD_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def dashboard_logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(DASHBOARD_COOKIE, path="/", samesite="strict")
    return response


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<h1>ROD</h1><p>Dashboard ausente.</p>", status_code=503)


@app.get("/api/system/status")
async def get_system_status():
    from jarvis.modules.system import SystemModule

    try:
        return JSONResponse(content=await SystemModule.get_raw_status())
    except Exception as exc:
        logger.exception("Falha ao coletar status do Pi")
        raise HTTPException(status_code=500, detail="System status unavailable") from exc


@app.get("/api/system/health")
async def get_system_health():
    from jarvis.database.persistence import Persistence

    checks: dict[str, dict[str, Any]] = {}
    try:
        Persistence.get_recent_events(1)
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    status = "ok" if all(check["ok"] for check in checks.values()) else "critical"
    return {
        "status": status,
        "timestamp": time.time(),
        "checks": checks,
        "assistant": {
            "name": "ROD",
            "mode": "local_skills",
            "generative_ai": False,
        },
    }


@app.get("/api/system/processes")
async def get_system_processes(limit: int = Query(20, ge=1, le=100)):
    import psutil

    processes = []
    candidates = psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"])
    for proc in sorted(
        candidates,
        key=lambda item: item.info.get("cpu_percent") or 0,
        reverse=True,
    )[:limit]:
        try:
            processes.append(
                {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "cpu": proc.info.get("cpu_percent") or 0,
                    "memory": proc.info.get("memory_percent") or 0,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"processes": processes}


@app.get("/api/system/logs")
async def get_system_logs(lines: int = Query(50, ge=1, le=200)):
    from jarvis.database.persistence import Persistence

    try:
        return {"logs": Persistence.get_recent_events(lines)}
    except Exception as exc:
        logger.exception("Falha ao ler eventos")
        return {"logs": [], "error": type(exc).__name__}


@app.get("/api/network/scan")
async def scan_network(depth: str = Query("standard", pattern="^(standard|deep|quick)$")):
    from jarvis.modules.network import NetworkModule

    try:
        if depth == "deep":
            devices = await NetworkModule.scan_network_deep()
        else:
            snapshot = await NetworkModule.get_raw_snapshot()
            devices = snapshot.get("devices", [])
        return {"devices": devices, "count": len(devices), "depth": depth}
    except Exception as exc:
        logger.exception("Falha ao varrer a rede")
        raise HTTPException(status_code=500, detail="Network scan unavailable") from exc


@app.get("/api/network/speedtest")
async def run_speedtest():
    from jarvis.modules.network import NetworkModule

    return {
        "result": await NetworkModule.run_speedtest(),
        "measurement_node": "raspberry_pi",
        "ethernet_limit_mbps": 100,
    }


@app.get("/api/network/ping")
async def check_ping():
    from jarvis.modules.network import NetworkModule

    return await NetworkModule.get_ping_metrics()


@app.get("/api/hydration/status")
async def get_hydration_status():
    from jarvis.config import Config
    from jarvis.modules.hydration import HydrationModule

    return {"status": HydrationModule.get_status_message(Config.ALLOWED_USER_ID)}


@app.get("/api/hydration/history")
async def get_hydration_history(days: int = Query(30, ge=1, le=90)):
    from jarvis.config import Config
    from jarvis.database.persistence import Persistence

    history = Persistence.get_hydration_history(Config.ALLOWED_USER_ID, days)
    return {"history": history, "days": days, "count": len(history)}


@app.get("/api/automations")
async def list_automations():
    automation = get_service("automation_service")
    return {"automations": automation.rules if automation else []}


@app.post("/api/automations/{rule_id}/toggle")
async def toggle_automation(rule_id: str):
    automation = service_required("automation_service")
    for rule in automation.rules:
        if rule["id"] == rule_id:
            rule["enabled"] = not rule["enabled"]
            return {"status": "success", "rule_id": rule_id, "enabled": rule["enabled"]}
    raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")


@app.get("/api/dashboard")
async def get_dashboard_data():
    from jarvis.config import Config
    from jarvis.modules.network import NetworkModule
    from jarvis.modules.system import SystemModule

    data: dict[str, Any] = {"timestamp": time.time()}
    try:
        data["system"] = await SystemModule.get_raw_status()
    except Exception as exc:
        data["system"] = {"error": type(exc).__name__}

    try:
        data["internet"] = await NetworkModule.get_ping_metrics()
    except Exception as exc:
        data["internet"] = {"success": False, "error": type(exc).__name__}

    data["bot"] = {
        "timezone": Config.TIMEZONE,
        "assistant_mode": "local_skills",
        "generative_ai": False,
    }
    return JSONResponse(content=data)


@app.get("/api/config")
async def get_config():
    from jarvis.config import Config

    return {
        "timezone": Config.TIMEZONE,
        "assistant_mode": "local_skills",
        "generative_ai": False,
        "intent_confidence": Config.INTENT_CONFIDENCE_THRESHOLD,
        "scheduler_interval": Config.SCHEDULER_INTERVAL_SECONDS,
        "hydration_interval": Config.HYDRATION_MIN_INTERVAL_MINUTES,
        "wake_on_lan_configured": bool(Config.PC_MAC),
        "pc_status_configured": bool(Config.PC_IP),
    }


@app.get("/api/events/recent")
async def get_recent_events(limit: int = Query(20, ge=1, le=100)):
    from jarvis.database.persistence import Persistence

    try:
        return {"events": Persistence.get_recent_events(limit)}
    except Exception as exc:
        logger.exception("Falha ao ler eventos")
        return {"events": [], "error": type(exc).__name__}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled API exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )
