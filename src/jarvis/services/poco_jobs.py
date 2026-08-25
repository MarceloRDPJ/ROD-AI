"""Shared client for jobs executed by the dedicated Poco node."""

from __future__ import annotations

import asyncio
import time

from jarvis.config import Config


async def run_poco_job(
    action: str,
    timeout_seconds: int = 70,
    params: dict | None = None,
):
    """Enqueue a Poco job and wait for its durable result.

    A stale heartbeat is not proof that the phone is offline. Android can delay
    the heartbeat executor while the foreground agent and its job poller are
    still reachable. The active job is the authoritative connectivity probe.
    """
    if not Config.POCO_NODE_ENABLED:
        return None, "O nó Poco está desativado na configuração."

    from jarvis.api.app import get_poco_service

    service = get_poco_service()
    status = service.status()
    if not status.get("heartbeat"):
        return None, "O Poco ainda não estabeleceu uma sessão com o Pi."

    job = service.enqueue(action, params=params or {}, ttl_seconds=timeout_seconds + 30)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = service.get_job(job.job_id)
        if current and current.status == "completed":
            return current.result or {}, None
        if current and current.status in {"failed", "expired"}:
            return None, current.error or "A tarefa expirou antes de concluir."
        await asyncio.sleep(2)
    return None, "O Poco não concluiu a tarefa dentro do tempo esperado."
