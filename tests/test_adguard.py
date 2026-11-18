import pytest
from unittest.mock import AsyncMock, patch

from jarvis.modules.adguard import AdGuardClient


@pytest.mark.asyncio
async def test_block_client_preserves_existing_rules():
    with patch.object(
        AdGuardClient,
        "_access_list",
        AsyncMock(
            return_value=(
                200,
                {
                    "allowed_clients": [],
                    "disallowed_clients": ["192.168.0.50"],
                    "blocked_hosts": ["telemetry.example"],
                },
            )
        ),
    ), patch.object(
        AdGuardClient,
        "_replace_access_list",
        AsyncMock(return_value=(200, "")),
    ) as replace:
        result = await AdGuardClient.block_client("192.168.0.100")

    assert result["success"] is True
    replace.assert_awaited_once_with(
        {
            "allowed_clients": [],
            "disallowed_clients": ["192.168.0.50", "192.168.0.100"],
            "blocked_hosts": ["telemetry.example"],
        }
    )


@pytest.mark.asyncio
async def test_block_client_is_idempotent():
    access = {
        "allowed_clients": [],
        "disallowed_clients": ["192.168.0.100"],
        "blocked_hosts": [],
    }
    with patch.object(AdGuardClient, "_access_list", AsyncMock(return_value=(200, access))), patch.object(
        AdGuardClient, "_replace_access_list", new_callable=AsyncMock
    ) as replace:
        result = await AdGuardClient.block_client("192.168.0.100")

    assert result["success"] is True
    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_block_client_rejects_domain_as_ip():
    result = await AdGuardClient.block_client("youtube.com")
    assert result["success"] is False
    assert "ip inválido" in result["message"].lower()


@pytest.mark.asyncio
async def test_block_domain_uses_adguard_rule():
    with patch.object(AdGuardClient, "_user_rules", AsyncMock(return_value=(200, []))), patch.object(
        AdGuardClient, "_replace_user_rules", AsyncMock(return_value=(200, ""))
    ) as replace:
        result = await AdGuardClient.block_domain("https://youtube.com/watch?v=1")

    assert result["success"] is True
    replace.assert_awaited_once_with(["||youtube.com^"])


@pytest.mark.asyncio
async def test_unblock_removes_only_target_rule():
    with patch.object(
        AdGuardClient,
        "_access_list",
        AsyncMock(
            return_value=(
                200,
                {
                    "allowed_clients": ["192.168.0.2"],
                    "disallowed_clients": ["192.168.0.100", "192.168.0.101"],
                    "blocked_hosts": ["other.example"],
                },
            )
        ),
    ), patch.object(
        AdGuardClient,
        "_replace_access_list",
        AsyncMock(return_value=(200, "")),
    ) as replace:
        result = await AdGuardClient.unblock_client("192.168.0.100")

    assert result["success"] is True
    replace.assert_awaited_once_with(
        {
            "allowed_clients": ["192.168.0.2"],
            "disallowed_clients": ["192.168.0.101"],
            "blocked_hosts": ["other.example"],
        }
    )
