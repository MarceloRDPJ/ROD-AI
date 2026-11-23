"""Contratos de agendamento dos serviços que rodam em laço no Pi.

Cobre três defeitos observados em produção:

1. O guardião anunciava "a energia caiu" para qualquer intervalo sem snapshot,
   inclusive quando só o contêiner havia reiniciado e o Pi nunca desligou.
2. Relatórios e automações comparavam o minuto por igualdade exata; o laço de
   60s derivava e o disparo era perdido, ou acontecia duas vezes.
3. As automações liam a hora local ingênua, que no contêiner não corresponde ao
   fuso configurado.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from jarvis.config import Config
from jarvis.core.context_reader import ContextReader
from jarvis.core.utils import schedule_is_due
from jarvis.services.automations import AutomationEngine
from jarvis.services.guardian import GuardianService
from jarvis.services.reporter import ReporterService
from jarvis.modules.system import SystemModule


def _local(year=2026, month=11, day=23, hour=7, minute=0, second=0):
    return Config.TZ.localize(datetime(year, month, day, hour, minute, second))


# =====================================================================
# schedule_is_due
# =====================================================================
def test_schedule_fires_when_loop_wakes_slightly_after_the_exact_minute():
    """O laço raramente acorda no segundo zero; perder o disparo por isso era o bug."""
    assert schedule_is_due(
        _local(hour=7, minute=0, second=43),
        7,
        0,
        period_key="2026-11-23",
        last_fired=None,
    )


def test_schedule_does_not_fire_before_the_scheduled_moment():
    assert not schedule_is_due(
        _local(hour=6, minute=59),
        7,
        0,
        period_key="2026-11-23",
        last_fired=None,
    )


def test_schedule_fires_only_once_per_period():
    assert not schedule_is_due(
        _local(hour=7, minute=2),
        7,
        0,
        period_key="2026-11-23",
        last_fired="2026-11-23",
    )


def test_schedule_fires_again_on_the_next_period():
    assert schedule_is_due(
        _local(day=24, hour=7, minute=0),
        7,
        0,
        period_key="2026-11-24",
        last_fired="2026-11-23",
    )


def test_schedule_does_not_resend_a_moment_that_passed_hours_ago():
    """Depois de um restart às 18h, o 'bom dia' das 7h não pode ser enviado atrasado."""
    assert not schedule_is_due(
        _local(hour=18),
        7,
        0,
        period_key="2026-11-23",
        last_fired=None,
    )


# =====================================================================
# Guardian: atribuição de causa do intervalo
# =====================================================================
@pytest.mark.asyncio
async def test_guardian_does_not_blame_power_when_the_pi_never_rebooted(monkeypatch):
    """Reinício de serviço não é queda de energia: o Pi seguia ligado há horas."""
    monkeypatch.setattr(
        ContextReader, "detect_gap",
        lambda max_gap_minutes=2: {"status": "ok", "exceeded": True, "gap_minutes": 3.0},
    )
    monkeypatch.setattr(
        SystemModule, "get_raw_status",
        AsyncMock(return_value={"uptime_seconds": 9 * 3600}),
    )
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    assert guardian.pending_power_msg is None


@pytest.mark.asyncio
async def test_guardian_reports_reboot_without_naming_a_cause(monkeypatch):
    """Watchdog, trava e queda de energia dão o mesmo reboot; o ROD não escolhe um."""
    monkeypatch.setattr(
        ContextReader, "detect_gap",
        lambda max_gap_minutes=2: {"status": "ok", "exceeded": True, "gap_minutes": 42.0},
    )
    monkeypatch.setattr(
        SystemModule, "get_raw_status",
        AsyncMock(return_value={"uptime_seconds": 120}),
    )
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    msg = guardian.pending_power_msg
    assert "42" in msg
    assert "o Pi reiniciou" in msg
    assert "energia caiu" not in msg


@pytest.mark.asyncio
async def test_guardian_reports_long_service_outage_without_blaming_power(monkeypatch):
    monkeypatch.setattr(
        ContextReader, "detect_gap",
        lambda max_gap_minutes=2: {"status": "ok", "exceeded": True, "gap_minutes": 90.0},
    )
    monkeypatch.setattr(
        SystemModule, "get_raw_status",
        AsyncMock(return_value={"uptime_seconds": 20 * 3600}),
    )
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    assert "não foi queda de energia" in guardian.pending_power_msg


@pytest.mark.asyncio
async def test_guardian_stays_silent_when_host_uptime_is_unavailable(monkeypatch):
    """Sem uptime não há evidência; o guardião não inventa a causa."""
    monkeypatch.setattr(
        ContextReader, "detect_gap",
        lambda max_gap_minutes=2: {"status": "ok", "exceeded": True, "gap_minutes": 30.0},
    )
    monkeypatch.setattr(
        SystemModule, "get_raw_status",
        AsyncMock(side_effect=OSError("psutil indisponivel")),
    )
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    assert guardian.pending_power_msg is None


@pytest.mark.asyncio
async def test_guardian_stays_silent_when_there_was_no_gap(monkeypatch):
    monkeypatch.setattr(
        ContextReader, "detect_gap",
        lambda max_gap_minutes=2: {"status": "ok", "exceeded": False, "gap_minutes": 0.5},
    )
    status = AsyncMock()
    monkeypatch.setattr(SystemModule, "get_raw_status", status)
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    assert guardian.pending_power_msg is None
    status.assert_not_awaited()


# =====================================================================
# Reporter
# =====================================================================
@pytest.mark.asyncio
async def test_daily_report_survives_loop_drift(monkeypatch):
    reporter = ReporterService(AsyncMock())
    sent = AsyncMock()
    monkeypatch.setattr(reporter, "_send_daily_report", sent)

    await reporter._check_schedule(_local(hour=7, minute=1, second=37))

    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_report_is_not_sent_twice_in_the_same_day(monkeypatch):
    reporter = ReporterService(AsyncMock())
    sent = AsyncMock()
    monkeypatch.setattr(reporter, "_send_daily_report", sent)

    await reporter._check_schedule(_local(hour=7, minute=0, second=10))
    await reporter._check_schedule(_local(hour=7, minute=0, second=55))

    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_weekly_report_only_on_monday(monkeypatch):
    reporter = ReporterService(AsyncMock())
    weekly = AsyncMock()
    monkeypatch.setattr(reporter, "_send_daily_report", AsyncMock())
    monkeypatch.setattr(reporter, "_send_weekly_report", weekly)

    tuesday = _local(day=24, hour=7, minute=5)
    assert tuesday.weekday() != ReporterService.WEEKLY_WEEKDAY
    await reporter._check_schedule(tuesday)

    weekly.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_report_fires_on_monday_within_window(monkeypatch):
    reporter = ReporterService(AsyncMock())
    weekly = AsyncMock()
    monkeypatch.setattr(reporter, "_send_daily_report", AsyncMock())
    monkeypatch.setattr(reporter, "_send_weekly_report", weekly)

    monday = _local(hour=7, minute=6)
    assert monday.weekday() == ReporterService.WEEKLY_WEEKDAY
    await reporter._check_schedule(monday)

    weekly.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_report_reports_system_failure_instead_of_inventing_data(monkeypatch):
    monkeypatch.setattr(
        SystemModule, "get_raw_status", AsyncMock(side_effect=OSError("sem /proc"))
    )
    from jarvis.modules.network import NetworkModule
    monkeypatch.setattr(
        NetworkModule, "get_ping_metrics",
        AsyncMock(return_value={"success": True, "latency_ms": 12}),
    )
    reporter = ReporterService(AsyncMock())

    report = await reporter._build_daily_report()

    assert "Aviso sistema: dados indisponiveis" in report
    assert "Online (12ms)" in report


@pytest.mark.asyncio
async def test_daily_report_delivery_failure_does_not_raise(monkeypatch):
    app = AsyncMock()
    app.bot.send_message.side_effect = OSError("telegram offline")
    reporter = ReporterService(app)
    monkeypatch.setattr(reporter, "_build_daily_report", AsyncMock(return_value="relatorio"))

    await reporter._send_daily_report()

    app.bot.send_message.assert_awaited_once()


# =====================================================================
# AutomationEngine
# =====================================================================
def _engine_with_time_rule(time_str="22:00"):
    engine = AutomationEngine(AsyncMock())
    engine.rules = [{
        "id": "auto_test",
        "name": "Regra de teste",
        "enabled": True,
        "trigger": {"type": "time", "time": time_str},
        "action": {"type": "notify", "message": "disparou"},
    }]
    return engine


@pytest.mark.asyncio
async def test_time_automation_fires_once_even_if_loop_runs_twice_in_the_minute(monkeypatch):
    engine = _engine_with_time_rule()
    executed = AsyncMock()
    monkeypatch.setattr(engine, "_execute_action", executed)
    monkeypatch.setattr(
        "jarvis.services.automations.datetime",
        _FrozenDatetime([_local(hour=22, minute=0, second=5), _local(hour=22, minute=0, second=58)]),
    )

    await engine._check_time_triggers()
    await engine._check_time_triggers()

    executed.assert_awaited_once()


@pytest.mark.asyncio
async def test_time_automation_uses_configured_timezone(monkeypatch):
    """No contêiner a hora local ingênua não é o fuso configurado."""
    engine = _engine_with_time_rule()
    executed = AsyncMock()
    monkeypatch.setattr(engine, "_execute_action", executed)
    monkeypatch.setattr(
        "jarvis.services.automations.datetime",
        _FrozenDatetime([_local(hour=22, minute=0)]),
    )

    await engine._check_time_triggers()

    executed.assert_awaited_once()
    assert engine._last_fired_day["auto_test"] == "2026-11-23"


@pytest.mark.asyncio
async def test_time_automation_ignores_invalid_schedule(monkeypatch):
    engine = _engine_with_time_rule(time_str="vinte e duas")
    executed = AsyncMock()
    monkeypatch.setattr(engine, "_execute_action", executed)
    monkeypatch.setattr(
        "jarvis.services.automations.datetime",
        _FrozenDatetime([_local(hour=22, minute=0)]),
    )

    await engine._check_time_triggers()

    executed.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_automation_never_fires(monkeypatch):
    engine = _engine_with_time_rule()
    engine.rules[0]["enabled"] = False
    executed = AsyncMock()
    monkeypatch.setattr(engine, "_execute_action", executed)
    monkeypatch.setattr(
        "jarvis.services.automations.datetime",
        _FrozenDatetime([_local(hour=22, minute=0)]),
    )

    await engine._check_time_triggers()

    executed.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_automation_runs_callable_message_and_markup():
    engine = AutomationEngine(AsyncMock())
    engine.rules = [{
        "id": "auto_event",
        "name": "Dispositivo",
        "enabled": True,
        "trigger": {"type": "event", "event_type": "network.unknown_device",
                    "condition": lambda e: True},
        "action": {
            "type": "notify",
            "message": lambda e: f"novo: {e.payload['ip']}",
            "reply_markup": lambda e: f"markup:{e.payload['ip']}",
        },
    }]

    from jarvis.core.events import Event
    await engine.on_event(Event("network.unknown_device", "guardian", {"ip": "192.168.1.77"}))

    engine.app.bot.send_message.assert_awaited_once()
    kwargs = engine.app.bot.send_message.await_args.kwargs
    assert kwargs["text"] == "novo: 192.168.1.77"
    assert kwargs["reply_markup"] == "markup:192.168.1.77"


@pytest.mark.asyncio
async def test_event_automation_respects_condition():
    engine = AutomationEngine(AsyncMock())
    engine.rules = [{
        "id": "auto_event",
        "name": "Internet caiu",
        "enabled": True,
        "trigger": {"type": "event", "event_type": "network.status_changed",
                    "condition": lambda e: e.payload.get("status") == "down"},
        "action": {"type": "notify", "message": "caiu"},
    }]

    from jarvis.core.events import Event
    await engine.on_event(Event("network.status_changed", "guardian", {"status": "up"}))

    engine.app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_automation_delivery_failure_does_not_raise():
    engine = AutomationEngine(AsyncMock())
    engine.app.bot.send_message.side_effect = OSError("telegram offline")

    await engine._execute_action({"type": "notify", "message": "oi"}, None)

    engine.app.bot.send_message.assert_awaited_once()


class _FrozenDatetime:
    """Substitui datetime.now() por uma sequência determinística de instantes."""

    def __init__(self, moments):
        self._moments = list(moments)

    def now(self, tz=None):
        moment = self._moments.pop(0) if len(self._moments) > 1 else self._moments[0]
        return moment.astimezone(tz) if tz else moment

    def __getattr__(self, name):
        return getattr(datetime, name)


def test_frozen_datetime_helper_advances_through_moments():
    frozen = _FrozenDatetime([_local(hour=1), _local(hour=2)])

    assert frozen.now(Config.TZ).hour == 1
    assert frozen.now(Config.TZ).hour == 2
    assert frozen.now(Config.TZ).hour == 2
    assert frozen.min == datetime.min
