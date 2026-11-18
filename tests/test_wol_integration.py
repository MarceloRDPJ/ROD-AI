"""
Teste de Integração - Wake-on-LAN
Valida que todas as correções foram implementadas corretamente.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

def test_config_tem_pc_mac():
    """Valida que PC_MAC está configurado"""
    from jarvis.config import Config

    assert hasattr(Config, 'PC_MAC'), "Config não tem PC_MAC"
    assert Config.PC_MAC is not None, "PC_MAC é None"

def test_intent_engine_reconhece_wake_pc():
    """Valida que Intent Engine reconhece comandos WOL"""
    from jarvis.nlp.intent_engine import detect_intent

    comandos = [
        "ligar o pc",
        "acordar computador",
        "wake on lan"
    ]

    for cmd in comandos:
        intent_data = detect_intent(cmd)
        assert intent_data is not None, f"Não reconheceu: {cmd}"
        assert intent_data.get("intent") == "wake_pc", f"Intent errado para: {cmd}"

@pytest.mark.asyncio
async def test_network_wake_on_lan_existe():
    """Valida que função wake_on_lan existe"""
    from jarvis.modules.network import NetworkModule

    assert hasattr(NetworkModule, 'wake_on_lan'), "NetworkModule não tem wake_on_lan"

    # Testa com MAC inválido
    result = await NetworkModule.wake_on_lan("INVALIDO")
    assert result['success'] == False, "Deveria rejeitar MAC inválido"

    # Testa com MAC válido (mock)
    with patch('wakeonlan.send_magic_packet'):
        result = await NetworkModule.wake_on_lan("AA:BB:CC:DD:EE:FF")
        assert result['success'] == True, "Deveria aceitar MAC válido"
        assert "AA:BB:CC:DD:EE:FF" not in result['message']


@pytest.mark.asyncio
async def test_executor_confirma_wol_sem_expor_mac(monkeypatch):
    """Identificador de rede privado não deve aparecer no Telegram."""
    from jarvis.core.executor import Executor
    from jarvis.config import Config

    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 1, raising=False)
    monkeypatch.setattr(Config, "PC_MAC", "AA:BB:CC:DD:EE:FF", raising=False)
    executor = Executor(MagicMock())

    text = await executor.execute(
        {"intent": "wake_pc", "action": "request", "params": {}},
        chat_id=1,
    )

    assert "confirmar" in text.lower()
    assert Config.PC_MAC not in text


@pytest.mark.asyncio
async def test_pc_status_uses_config_but_does_not_expose_private_ip(monkeypatch):
    from jarvis.core.executor import Executor
    from jarvis.config import Config

    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 1, raising=False)
    monkeypatch.setattr(Config, "PC_IP", "192.168.1.50", raising=False)
    monkeypatch.setattr(
        "jarvis.core.executor.NetworkModule.check_device_online",
        AsyncMock(return_value=True),
    )
    executor = Executor(MagicMock())

    text = await executor.execute(
        {"intent": "pc_status", "action": "check", "params": {}},
        chat_id=1,
    )

    assert "ONLINE" in text
    assert Config.PC_IP not in text

@pytest.mark.asyncio
async def test_executor_valida_chat_id():
    """Valida que Executor rejeita chat_id não autorizado"""
    from jarvis.core.executor import Executor
    from jarvis.config import Config

    # Mock application
    mock_app = MagicMock()
    executor = Executor(mock_app)

    # Chat não autorizado
    resultado = await executor.execute(
        {"intent": "network_scan", "action": "scan"},
        chat_id=999999999  # ID falso
    )

    assert "negado" in resultado.lower() or "não autorizado" in resultado.lower(), \
        "Executor não bloqueou chat_id não autorizado"

@pytest.mark.asyncio
async def test_persistence_fecha_conexoes():
    """Valida que Persistence usa context managers"""
    import inspect
    from jarvis.database.persistence import Persistence

    # Pega código fonte da função get_state
    source = inspect.getsource(Persistence.get_state)

    assert "with closing(sqlite3.connect" in source, \
        "get_state não usa context manager (with)"

def test_todas_funcoes_persistence_usam_with():
    """Valida que TODAS as funções em Persistence usam with"""
    import inspect
    from jarvis.database import persistence

    # Pega todas as funções do módulo
    functions = [
        name for name, obj in inspect.getmembers(persistence)
        if inspect.isfunction(obj) and 'sqlite3.connect' in inspect.getsource(obj)
    ]

    problemas = []
    for func_name in functions:
        func = getattr(persistence, func_name)
        source = inspect.getsource(func)

        if 'sqlite3.connect' in source and 'with closing(sqlite3.connect' not in source:
            problemas.append(func_name)

    assert len(problemas) == 0, \
        f"Funções SEM context manager: {', '.join(problemas)}"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
