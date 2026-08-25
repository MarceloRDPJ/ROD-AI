"""Reliable, signed job queue for the dedicated Android node."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# Only actions the agent really implements. Advertising more meant the job was
# queued, dispatched and only rejected by the phone after the full timeout.
ALLOWED_ACTIONS = frozenset(
    {
        "device_status",
        "network_check",
        "read_bill_cache",
        "refresh_equatorial_bills",
        "clara_equatorial_bills",
        "refresh_saneago_bills",
        # Artefatos sob demanda de uma fatura já consultada. Nenhuma das duas
        # paga, confirma ou movimenta nada: uma devolve o Pix copia e cola, a
        # outra devolve o identificador opaco do PDF oficial já baixado.
        "get_equatorial_pix",
        "get_equatorial_boleto",
    }
)

TERMINAL_STATES = frozenset({"completed", "failed", "expired"})

# Campos que não podem existir no disco em nenhum momento. O resultado do job é
# gravado no mesmo instante em que chega, então filtrar só na poda seria tarde:
# o valor já teria tocado o arquivo.
SENSITIVE_RESULT_KEYS = frozenset({"pix", "barcode", "payload", "qr", "pdf", "pdf_base64"})

# Ações cujo resultado inteiro é sensível. Para elas o disco guarda o registro do
# job — status, ação, horário — e nada do conteúdo.
MEMORY_ONLY_RESULT_ACTIONS = frozenset({"get_equatorial_pix"})


@dataclass
class PocoJob:
    job_id: str
    action: str
    params: dict[str, Any]
    created_at: float
    expires_at: float
    status: str = "queued"
    updated_at: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0


class PocoAuthenticationError(ValueError):
    pass


class PocoNodeService:
    """Small persistent queue. It never exposes arbitrary command execution."""

    def __init__(
        self,
        storage_path: str | os.PathLike,
        shared_secret: str = "",
        signature_max_age_seconds: int = 120,
        heartbeat_stale_seconds: int = 150,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        result_grace_seconds: int = 600,
        sensitive_result_grace_seconds: int = 120,
        retention_seconds: int = 3600,
        clock=time.time,
    ):
        self.storage_path = Path(storage_path)
        self.shared_secret = shared_secret
        self.signature_max_age_seconds = signature_max_age_seconds
        self.heartbeat_stale_seconds = heartbeat_stale_seconds
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.result_grace_seconds = result_grace_seconds
        self.sensitive_result_grace_seconds = min(
            sensitive_result_grace_seconds, result_grace_seconds
        )
        self.retention_seconds = retention_seconds
        self.clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[str, PocoJob] = {}
        self._heartbeat: dict[str, Any] | None = None
        self._load()

    @staticmethod
    def signature(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
        body_hash = hashlib.sha256(body).hexdigest()
        canonical = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}".encode()
        return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()

    def authenticate(
        self,
        timestamp: str | None,
        signature: str | None,
        method: str,
        path: str,
        body: bytes,
    ) -> None:
        if not self.shared_secret:
            raise PocoAuthenticationError("Poco node secret is not configured")
        if not timestamp or not signature:
            raise PocoAuthenticationError("Missing Poco authentication headers")
        try:
            sent_at = int(timestamp)
        except ValueError as exc:
            raise PocoAuthenticationError("Invalid Poco timestamp") from exc
        if abs(self.clock() - sent_at) > self.signature_max_age_seconds:
            raise PocoAuthenticationError("Expired Poco request")
        expected = self.signature(self.shared_secret, timestamp, method, path, body)
        if not hmac.compare_digest(expected, signature):
            raise PocoAuthenticationError("Invalid Poco signature")

    def enqueue(self, action: str, params: dict[str, Any] | None = None, ttl_seconds: int = 180) -> PocoJob:
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported Poco action: {action}")
        now = self.clock()
        job = PocoJob(
            job_id=uuid.uuid4().hex,
            action=action,
            params=params or {},
            created_at=now,
            expires_at=now + max(10, min(ttl_seconds, 900)),
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._save()
        return job

    def next_job(self) -> PocoJob | None:
        now = self.clock()
        with self._lock:
            changed = self._sweep(now)
            for job in sorted(self._jobs.values(), key=lambda item: item.created_at):
                if job.status == "queued":
                    job.status = "accepted"
                    job.attempts += 1
                    job.updated_at = now
                    self._save()
                    return job
            if changed:
                self._save()
        return None

    def _prune(self, now: float) -> bool:
        """Descarta dados de pagamento e jobs velhos.

        O ``result`` de uma consulta de fatura carrega valor, linha digitável e
        PIX em texto claro, e este arquivo fica ao lado de um dashboard sem
        autenticação documentada. O consumidor legítimo lê o resultado em
        segundos, dentro do timeout do job; guardá-lo por mais tempo não serve a
        ninguém e a fila crescia sem limite — dezenas de faturas acumuladas.
        O registro do job continua, para auditoria; só o conteúdo sai.

        O Pix copia e cola é o caso mais sensível de todos e por isso tem prazo
        próprio, mais curto: quem pediu o artefato está com o Telegram aberto
        naquele minuto. Isto estende o mecanismo existente, não o substitui.
        """
        changed = False
        for job in list(self._jobs.values()):
            if job.status not in TERMINAL_STATES:
                continue
            age = now - job.updated_at
            if age > self.retention_seconds:
                del self._jobs[job.job_id]
                changed = True
            elif job.result and age > self._grace_for(job):
                job.result = None
                changed = True
        return changed

    def _grace_for(self, job: PocoJob) -> int:
        if job.action in MEMORY_ONLY_RESULT_ACTIONS:
            return self.sensitive_result_grace_seconds
        return self.result_grace_seconds

    def _sweep(self, now: float) -> bool:
        """Expire dead jobs and return abandoned leases to the queue.

        A job goes to ``accepted`` the moment it is handed out. If the Wi-Fi drops
        exactly then, the node never sees it and the job would sit there until the
        TTL while the user waits. Handing it back after the lease gives the node a
        second chance inside the same request. The agent deduplicates by ``job_id``,
        so a redelivered job is never executed twice.
        """
        changed = self._prune(now)
        for job in list(self._jobs.values()):
            if job.status in {"queued", "accepted", "running"} and job.expires_at <= now:
                job.status = "expired"
                job.updated_at = now
                changed = True
            elif job.status == "accepted" and now - job.updated_at > self.lease_seconds:
                if job.attempts >= self.max_attempts:
                    job.status = "failed"
                    job.error = "O Poco não confirmou o início da tarefa"
                else:
                    job.status = "queued"
                job.updated_at = now
                changed = True
        return changed

    def get_job(self, job_id: str) -> PocoJob | None:
        """Return a detached snapshot so callers cannot mutate queue state."""
        with self._lock:
            job = self._jobs.get(job_id)
            return PocoJob(**asdict(job)) if job else None

    def update_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> PocoJob:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("Invalid Poco job state")
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.status in TERMINAL_STATES and job.status != "expired":
                return job
            allowed = {
                # A lease can be requeued while the node is still working on it; its
                # durable outbox will report the real outcome, so accept it here
                # instead of answering 4xx and making the node drop a real result.
                "queued": {"running", "completed", "failed"},
                "accepted": {"running", "completed", "failed"},
                "running": {"completed", "failed"},
                # O nó pode concluir depois do TTL e só então conseguir entregar.
                # Recusar aqui fazia o agente tratar como rejeição definitiva e
                # descartar uma leitura real que custou minutos de automação.
                "expired": {"completed", "failed"},
            }
            if status not in allowed.get(job.status, set()):
                raise ValueError(f"Invalid transition: {job.status} -> {status}")
            job.status = status
            job.updated_at = self.clock()
            job.result = result if status == "completed" else None
            job.error = str(error)[:500] if error and status == "failed" else None
            self._save()
            return job

    def record_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed_properties = {"kitnet_01", "kitnet_02", "sala_comercial", "casa", "restaurante"}

        def property_names(field: str) -> list[str]:
            value = payload.get(field, [])
            if not isinstance(value, list):
                return []
            # Lista fechada: o heartbeat não vira um canal para exfiltrar UC,
            # documento ou qualquer texto arbitrário do cofre Android.
            return [item for item in dict.fromkeys(value) if item in allowed_properties]

        safe = {
            "node_id": str(payload.get("node_id", ""))[:64],
            "battery_level": self._number(payload.get("battery_level"), 0, 100),
            "battery_temperature_c": self._number(payload.get("battery_temperature_c"), -20, 90),
            "thermal_status": str(payload.get("thermal_status", "unknown"))[:32],
            "wifi_connected": bool(payload.get("wifi_connected", False)),
            "agent_version": str(payload.get("agent_version", ""))[:32],
            # O nome da versão não distingue dois builds locais: os dois dizem
            # "1.0.2". O código da versão distingue, e é o único identificador do
            # binário que chega até aqui — sem ele o inventário do Pi não sabe
            # qual APK está no telefone. O agente já o enviava; esta lista é de
            # permissão explícita, então o campo vinha e era descartado em
            # silêncio, que é o pior dos dois mundos: custo no telefone, nenhum
            # ganho aqui.
            "agent_version_code": int(self._number(payload.get("agent_version_code"), 0, 10_000_000) or 0),
            "saneago_configured": bool(payload.get("saneago_configured", False)),
            "equatorial_configured": bool(payload.get("equatorial_configured", False)),
            "water_units": int(self._number(payload.get("water_units"), 0, 8) or 0),
            "energy_units": int(self._number(payload.get("energy_units"), 0, 8) or 0),
            "water_properties": property_names("water_properties"),
            "energy_properties": property_names("energy_properties"),
            "busy": bool(payload.get("busy", False)),
            "pending_results": int(self._number(payload.get("pending_results"), 0, 999) or 0),
            "received_at": self.clock(),
        }
        with self._lock:
            self._heartbeat = safe
            self._save()
        return safe

    def status(self) -> dict[str, Any]:
        with self._lock:
            heartbeat = dict(self._heartbeat) if self._heartbeat else None
            queued = sum(1 for job in self._jobs.values() if job.status == "queued")
            running = sum(1 for job in self._jobs.values() if job.status in {"accepted", "running"})
        online = bool(
            heartbeat
            and self.clock() - float(heartbeat["received_at"]) <= self.heartbeat_stale_seconds
        )
        return {"online": online, "heartbeat": heartbeat, "queued_jobs": queued, "active_jobs": running}

    @staticmethod
    def _number(value: Any, minimum: float, maximum: float) -> float | None:
        try:
            return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError):
            return None

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._jobs = {item["job_id"]: PocoJob(**item) for item in data.get("jobs", [])}
            self._heartbeat = data.get("heartbeat")
        except (OSError, ValueError, TypeError, KeyError):
            self._jobs = {}
            self._heartbeat = None

    def _serialize(self, job: PocoJob) -> dict[str, Any]:
        """Versão do job que pode existir no disco.

        A poda por prazo continua valendo, mas ela age depois. Pix, linha
        digitável e PDF não podem esperar prazo nenhum: a fila é gravada no
        mesmo instante em que o resultado chega, e ali é o único momento em que
        dá para impedir que o valor toque o arquivo. O consumidor legítimo lê o
        resultado da memória, dentro do timeout do job.
        """
        data = asdict(job)
        result = data.get("result")
        if not isinstance(result, dict):
            return data
        if job.action in MEMORY_ONLY_RESULT_ACTIONS:
            data["result"] = None
            return data
        data["result"] = {
            key: value for key, value in result.items() if key not in SENSITIVE_RESULT_KEYS
        }
        return data

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"jobs": [self._serialize(job) for job in self._jobs.values()], "heartbeat": self._heartbeat}
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.storage_path)
