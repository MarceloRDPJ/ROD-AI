from __future__ import annotations

from datetime import datetime
from typing import Any

from jarvis.database.persistence import Persistence
from jarvis.modules.adguard import AdGuardClient
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
        name = profile.get("name") or profile.get("custom_name")
        hostname = profile.get("hostname")
        vendor = profile.get("vendor")
        device_type = profile.get("device_type") or profile.get("guessed_type")
        ip = profile.get("last_ip") or data["identity"].get("ip") or "?"
        mac = data["identity"].get("mac") or profile.get("mac") or "?"
        title = "Novo dispositivo observado" if alert else "Dossie do dispositivo"

        identity_bits = [item for item in (name, hostname, device_type, vendor) if item and item != "Dispositivo Desconhecido"]
        identity_text = " / ".join(dict.fromkeys(identity_bits)) or "nao identificado"
        ips = [item.get("ip") for item in profile.get("ips", []) if item.get("ip")]

        lines = [
            f"🔎 *{title}*",
            f"Identificacao: *{identity_text}*",
            f"IP atual: `{ip}`",
            f"MAC: `{mac}`",
            f"Primeira vez: {DeviceIntelligence._date(profile.get('first_seen'))}",
            f"Ultima vez: {DeviceIntelligence._date(profile.get('last_seen'))}",
            f"Leituras do guardiao: {int(profile.get('seen_count') or 0)}",
            f"IPs ja usados: {', '.join(ips) if ips else ip}",
        ]

        if profile.get("mac_is_private"):
            lines.append("⚠️ MAC privado/aleatorio: o fabricante pelo prefixo nao e confiavel e o aparelho pode reaparecer com outro MAC.")
        elif vendor:
            lines.append(f"Fabricante pelo OUI: {vendor} (indicio, nao prova do modelo).")

        if activity.get("available"):
            count = activity.get("dns_queries_sampled", 0)
            domains = activity.get("top_domains", [])
            lines.append(f"Atividade DNS: {count} consultas na amostra; {activity.get('blocked_sampled', 0)} bloqueadas.")
            if domains:
                lines.append(f"Dominios mais vistos: {', '.join(domains)}")
        else:
            lines.append("Atividade DNS: AdGuard sem dados disponiveis para este IP.")

        known = bool(name)
        repeated = int(profile.get("seen_count") or 0) > 1
        if known:
            assessment = "cadastrado por voce; sem evidencia local de invasao."
        elif repeated:
            assessment = "ja apareceu antes, mas continua sem dono confirmado."
        else:
            assessment = "primeira observacao; identidade ainda insuficiente."
        lines.append(f"Avaliacao: {assessment}")
        lines.append("Limite: DNS nao mede bytes consumidos e o Pi nao sabe o comodo/local fisico sem telemetria do roteador ou AP.")
        return "\n".join(lines)
