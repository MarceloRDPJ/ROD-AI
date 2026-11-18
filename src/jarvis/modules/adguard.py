import base64
import logging
import re
from typing import Any

import aiohttp

from jarvis.config import Config

logger = logging.getLogger("modules.adguard")


class AdGuardClient:
    """Cliente mínimo para a API documentada do AdGuard Home."""

    BASE_URL = Config.get("adguard.url", "http://localhost:3000").rstrip("/")
    USERNAME = Config.get("adguard.username", "admin")
    PASSWORD = Config.get("adguard.password", "")

    @classmethod
    def _get_auth_header(cls) -> dict[str, str]:
        credentials = f"{cls.USERNAME}:{cls.PASSWORD}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    @classmethod
    async def _request(cls, method: str, path: str, **kwargs) -> tuple[int, Any]:
        url = f"{cls.BASE_URL}/control/{path.lstrip('/')}"
        headers = cls._get_auth_header()
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.request(method, url, **kwargs) as response:
                    if response.status == 200:
                        if response.content_type == "application/json":
                            return response.status, await response.json()
                        return response.status, await response.text()
                    body = await response.text()
                    logger.error("AdGuard %s %s falhou: %s - %s", method, path, response.status, body)
                    return response.status, body
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("AdGuard indisponível em %s: %s", path, exc)
            return 0, str(exc)

    @classmethod
    async def _user_rules(cls) -> tuple[int, list[str] | str]:
        status, payload = await cls._request("GET", "filtering/status")
        if status != 200 or not isinstance(payload, dict):
            return status, str(payload)
        rules = payload.get("user_rules", [])
        if not isinstance(rules, list):
            return 500, "Resposta do AdGuard sem user_rules válidas"
        return 200, [str(rule) for rule in rules]

    @classmethod
    async def _replace_user_rules(cls, rules: list[str]) -> tuple[int, Any]:
        return await cls._request("POST", "filtering/set_rules", json={"rules": rules})

    @classmethod
    async def _access_list(cls) -> tuple[int, dict[str, list[str]] | str]:
        status, payload = await cls._request("GET", "access/list")
        if status != 200 or not isinstance(payload, dict):
            return status, str(payload)
        access = {}
        for key in ("allowed_clients", "disallowed_clients", "blocked_hosts"):
            values = payload.get(key, [])
            if not isinstance(values, list):
                return 500, f"Resposta do AdGuard sem {key} válida"
            access[key] = [str(value) for value in values]
        return 200, access

    @classmethod
    async def _replace_access_list(cls, access: dict[str, list[str]]) -> tuple[int, Any]:
        return await cls._request("POST", "access/set", json=access)

    @classmethod
    async def _add_filter_rule(cls, rule: str) -> dict[str, Any]:
        status, rules = await cls._user_rules()
        if status != 200 or not isinstance(rules, list):
            return {"success": False, "message": f"Falha ao ler regras do AdGuard ({status}): {rules}"}
        if rule in rules:
            return {"success": True, "message": f"Regra já existe no AdGuard: {rule}"}
        status, body = await cls._replace_user_rules([*rules, rule])
        if status == 200:
            return {"success": True, "message": f"Regra adicionada ao AdGuard: {rule}"}
        return {"success": False, "message": f"Falha AdGuard ({status}): {body}"}

    @staticmethod
    def _is_ip(value: str) -> bool:
        return bool(re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value or "")) and all(
            0 <= int(part) <= 255 for part in value.split(".")
        )

    @staticmethod
    def _is_domain(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value or ""))

    @classmethod
    async def block_client(cls, ip: str, name: str | None = None) -> dict[str, Any]:
        del name
        if not cls._is_ip(ip):
            return {"success": False, "message": f"IP inválido para bloqueio de cliente: {ip}"}
        status, access = await cls._access_list()
        if status != 200 or not isinstance(access, dict):
            return {"success": False, "message": f"Falha ao ler acesso do AdGuard ({status}): {access}"}
        if ip in access["allowed_clients"]:
            return {
                "success": False,
                "message": "O cliente está na lista permitida do AdGuard; remova essa permissão manualmente antes de bloquear.",
            }
        if ip in access["disallowed_clients"]:
            return {"success": True, "message": f"Cliente {ip} já está bloqueado no AdGuard."}
        access["disallowed_clients"].append(ip)
        status, body = await cls._replace_access_list(access)
        if status == 200:
            return {"success": True, "message": f"Cliente {ip} bloqueado no AdGuard."}
        return {"success": False, "message": f"Falha AdGuard ({status}): {body}"}

    @classmethod
    async def block_domain(cls, domain: str, name: str | None = None) -> dict[str, Any]:
        del name
        domain = (domain or "").strip().lower()
        domain = domain.removeprefix("http://").removeprefix("https://").split("/")[0]
        if not cls._is_domain(domain):
            return {"success": False, "message": f"Domínio inválido: {domain}"}
        return await cls._add_filter_rule(f"||{domain}^")

    @classmethod
    async def unblock_client(cls, ip: str) -> dict[str, Any]:
        if not cls._is_ip(ip):
            return {"success": False, "message": f"IP inválido para desbloqueio: {ip}"}
        status, access = await cls._access_list()
        if status != 200 or not isinstance(access, dict):
            return {"success": False, "message": f"Falha ao ler acesso do AdGuard ({status}): {access}"}
        if ip not in access["disallowed_clients"]:
            return {"success": True, "message": f"Cliente {ip} já estava desbloqueado."}
        access["disallowed_clients"] = [item for item in access["disallowed_clients"] if item != ip]
        status, body = await cls._replace_access_list(access)
        if status == 200:
            return {"success": True, "message": f"Cliente {ip} desbloqueado."}
        return {"success": False, "message": f"Falha AdGuard ({status}): {body}"}

    @classmethod
    async def get_stats(cls) -> dict[str, Any]:
        status, data = await cls._request("GET", "stats")
        if status != 200 or not isinstance(data, dict):
            return {}
        return {
            "num_dns_queries": data.get("num_dns_queries", 0),
            "num_blocked_filtering": data.get("num_blocked_filtering", 0),
            "top_queried_domains": data.get("top_queried_domains", []),
            "top_clients": data.get("top_clients", []),
        }

    @classmethod
    async def get_top_clients(cls, limit: int = 5) -> list[dict[str, Any]]:
        clients = (await cls.get_stats()).get("top_clients", [])
        result = []
        for client in clients[: max(0, limit)]:
            if not isinstance(client, dict):
                continue
            ip = str(client.get("ip") or client.get("name") or "")
            result.append(
                {
                    "ip": ip,
                    "name": str(client.get("name") or ip),
                    "queries": int(client.get("count", client.get("num", 0)) or 0),
                    "blocked": int(client.get("blocked", client.get("blocked_count", 0)) or 0),
                }
            )
        return result
