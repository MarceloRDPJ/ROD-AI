from __future__ import annotations

from datetime import datetime
from typing import Any

from jarvis.database.persistence import Persistence
from jarvis.modules.adguard import AdGuardClient
from jarvis.core.telegram_safe import sanitize_inline_value
from jarvis.modules.network import NetworkModule


class DeviceIntelligence:
    """Builds an evidence-based device dossier from sources available on the Pi."""

    @staticmethod
    async def observe(ip: str, mac: str) -> dict[str, Any]:
        identity = await NetworkModule.identify_device(ip, mac)
        Persistence.register_device_seen(
            mac,
            ip=ip,
            hostname=identity.get("hostname"),
            vendor=identity.get("vendor"),
            device_type=identity.get("guessed_type"),
            mac_is_private=identity.get("mac_is_private", False),
        )
        return identity

    @staticmethod
    async def dossier(ip: str, mac: str, observe: bool = False) -> dict[str, Any]:
        identity = await DeviceIntelligence.observe(ip, mac) if observe else None
        profile = Persistence.get_device_profile(mac)
        if profile is None:
            identity = identity or await NetworkModule.identify_device(ip, mac)
            profile = {**identity, "first_seen": None, "last_seen": None, "seen_count": 0, "ips": []}
        activity = await AdGuardClient.get_client_activity(ip)
        return {"identity": identity or profile, "profile": profile, "activity": activity}

    @staticmethod
    def _date(value: str | None) -> str:
        if not value:
            return "sem registro"
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def format_dossier(data: dict[str, Any], *, alert: bool = False) -> str:
        profile = data["profile"]
        activity = data["activity"]
        # Nome, hostname e fabricante sao anunciados pelo proprio aparelho. O
        # Markdown legado do Telegram nao aceita escape, entao um hostname com
        # `*` ou `_` desformataria a mensagem inteira.
        name = sanitize_inline_value(profile.get("name") or profile.get("custom_name")) or None
        hostname = sanitize_inline_value(profile.get("hostname")) or None
        vendor = sanitize_inline_value(profile.get("vendor")) or None
        device_type = profile.get("device_type") or profile.get("guessed_type")
        ip = profile.get("last_ip") or data["identity"].get("ip") or "?"
        mac = data["identity"].get("mac") or profile.get("mac") or "?"
        title = "Novo dispositivo observado" if alert else "Dossiê do dispositivo"

        identity_bits = [item for item in (name, hostname, device_type, vendor) if item and item != "Dispositivo Desconhecido"]
        identity_text = " / ".join(dict.fromkeys(identity_bits)) or "não identificado"
        ips = [item.get("ip") for item in profile.get("ips", []) if item.get("ip")]

        lines = [
            f"🔎 *{title}*",
            f"Identificação: *{identity_text}*",
            f"IP atual: `{ip}`",
            f"MAC: `{mac}`",
            f"Primeira vez: {DeviceIntelligence._date(profile.get('first_seen'))}",
            f"Última vez: {DeviceIntelligence._date(profile.get('last_seen'))}",
            f"Leituras do guardião: {int(profile.get('seen_count') or 0)}",
            f"IPs já usados: {', '.join(ips) if ips else ip}",
        ]

        if profile.get("mac_is_private"):
            lines.append("⚠️ MAC privado/aleatório: o fabricante pelo prefixo não é confiável e o aparelho pode reaparecer com outro MAC.")
        elif vendor:
            lines.append(f"Fabricante pelo OUI: {vendor} (indício, não prova do modelo).")

        if activity.get("available"):
            count = activity.get("dns_queries_sampled", 0)
            domains = activity.get("top_domains", [])
            lines.append(f"Atividade DNS: {count} consultas na amostra; {activity.get('blocked_sampled', 0)} bloqueadas.")
            if domains:
                lines.append(f"Domínios mais vistos: {', '.join(domains)}")
        else:
            lines.append("Atividade DNS: AdGuard sem dados disponíveis para este IP.")

        known = bool(name)
        repeated = int(profile.get("seen_count") or 0) > 1
        if known:
            assessment = "cadastrado por você; sem evidência local de invasão."
        elif repeated:
            assessment = "já apareceu antes, mas continua sem dono confirmado."
        else:
            assessment = "primeira observação; identidade ainda insuficiente."
        lines.append(f"Avaliação: {assessment}")
        lines.append(
            "Limite: DNS não mede bytes consumidos, e o Pi não sabe o cômodo nem o local "
            "físico sem telemetria do roteador ou do ponto de acesso."
        )
        return "\n".join(lines)
