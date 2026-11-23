from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from jarvis.database.persistence import Persistence


class ContextEngine:
    """
    ContextEngine — Memória REAL do ROD.

    RESPONSABILIDADES (NUNCA VIOLAR):
    - Memória curta (chat)
    - Memória longa (snapshots RAW)
    - Baseline técnico (normalidade)
    - Comparação entre períodos
    - Gerenciar ações pendentes (autorização humana)
    - NÃO interpretar
    - NÃO gerar texto humano
    - NÃO executar ações
    """

    # ==================================================
    # MEMÓRIA CURTA (CHAT)
    # ==================================================
    @staticmethod
    def save_context(chat_id: int, data: dict) -> None:
        """
        Salva ou atualiza o contexto do chat.
        Faz merge com dados existentes.
        """
        current = ContextEngine.get_context(chat_id)
        current.update(data)

        # Atualiza timestamp e last_intent se disponível
        current["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "intent" in data:
            current["last_intent"] = data["intent"]

        Persistence.set_state(f"context:{chat_id}", current)

    @staticmethod
    def get_context(chat_id: int) -> Dict[str, Any]:
        return Persistence.get_state(f"context:{chat_id}", {}) or {}

    @staticmethod
    def last_action(chat_id: int) -> Optional[str]:
        return ContextEngine.get_context(chat_id).get("last_intent")

    @staticmethod
    def has_recent_activity(chat_id: int, minutes: int = 10) -> bool:
        ctx = ContextEngine.get_context(chat_id)
        ts = ctx.get("timestamp")
        if not ts:
            return False

        try:
            # Handle both naive and aware timestamps for compatibility
            last_ts = datetime.fromisoformat(ts)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)

            delta = datetime.now(timezone.utc) - last_ts
            return delta < timedelta(minutes=minutes)
        except Exception:
            return False

    # ==================================================
    # MEMÓRIA LONGA (SNAPSHOTS RAW)
    # ==================================================
    @staticmethod
    def get_recent_snapshots(minutes: int = 60) -> List[Dict[str, Any]]:
        """
        Retorna snapshots RAW dos últimos X minutos.
        Ordenados do mais recente para o mais antigo.
        """
        snapshots = Persistence.get_recent_snapshots(minutes) or []

        # Garantia de ordenação segura
        try:
            snapshots.sort(
                key=lambda s: datetime.fromisoformat(s["timestamp"]),
                reverse=True,
            )
        except Exception:
            pass

        return snapshots

    @staticmethod
    def get_snapshot_before(minutes: int) -> Optional[Dict[str, Any]]:
        """
        Retorna o snapshot mais próximo ANTES de X minutos.
        """
        return Persistence.get_snapshot_before(minutes)

    @staticmethod
    def get_last_snapshot() -> Optional[Dict[str, Any]]:
        """
        Retorna o último snapshot gravado (sem filtro de tempo).
        """
        return Persistence.get_last_snapshot()

    # ==================================================
    # BASELINE (NORMALIDADE)
    # ==================================================
    @staticmethod
    def update_baseline(snapshot: Dict[str, Any]) -> None:
        """
        Atualiza baseline técnico.
        Estratégia atual: último snapshot válido.
        """
        if snapshot:
            Persistence.save_baseline(snapshot)

    @staticmethod
    def get_baseline() -> Optional[Dict[str, Any]]:
        return Persistence.get_baseline()

    # ==================================================
    # AÇÕES PENDENTES (PASSO 10 — AUTORIZAÇÃO HUMANA)
    # ==================================================
    @staticmethod
    def set_pending_action(chat_id: int, action: Dict[str, Any]) -> None:
        Persistence.set_state(
            f"pending_action:{chat_id}",
            {
                "action": action,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def get_pending_action(chat_id: int) -> Optional[Dict[str, Any]]:
        return Persistence.get_state(f"pending_action:{chat_id}")

    @staticmethod
    def clear_pending_action(chat_id: int) -> None:
        Persistence.set_state(f"pending_action:{chat_id}", None)

    @staticmethod
    def set_network_target(chat_id: int, ip: str, mac: str | None = None) -> None:
        """Remember the device referenced by the latest network interaction."""
        ContextEngine.save_context(
            chat_id,
            {
                "network_target": {
                    "ip": ip,
                    "mac": mac,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    @staticmethod
    def get_recent_network_target(chat_id: int, minutes: int = 15) -> Optional[Dict[str, Any]]:
        target = ContextEngine.get_context(chat_id).get("network_target")
        if not isinstance(target, dict) or not target.get("ip") or not target.get("timestamp"):
            return None
        try:
            timestamp = datetime.fromisoformat(str(target["timestamp"]))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - timestamp > timedelta(minutes=minutes):
                return None
        except (TypeError, ValueError):
            return None
        return target

    # ==================================================
    # COMPARAÇÃO PROFUNDA (BASE DA INTELIGÊNCIA)
    # ==================================================
    @staticmethod
    def diff_snapshots(
        current: Dict[str, Any],
        previous: Dict[str, Any],
        prefix: str = "",
    ) -> Dict[str, Any]:
        """
        Compara dois snapshots RAW recursivamente.
        Retorna SOMENTE o que mudou.
        """
        diff: Dict[str, Any] = {}

        for key, value in current.items():
            # Ignorar timestamps internos voláteis
            if key in ("timestamp",):
                continue

            full_key = f"{prefix}.{key}" if prefix else key

            if key not in previous:
                diff[full_key] = {
                    "before": None,
                    "after": value,
                }
                continue

            old_value = previous[key]

            if isinstance(value, dict) and isinstance(old_value, dict):
                nested = ContextEngine.diff_snapshots(
                    value,
                    old_value,
                    prefix=full_key,
                )
                diff.update(nested)

            elif value != old_value:
                diff[full_key] = {
                    "before": old_value,
                    "after": value,
                }

        return diff
