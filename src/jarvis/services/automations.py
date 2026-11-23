import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List
from jarvis.config import Config
from jarvis.core.events import Event
from jarvis.core.telegram_safe import safe_send_message
from jarvis.core.utils import schedule_is_due

logger = logging.getLogger("services.automations")

class AutomationEngine:
    """
    Motor de automações do ROD.
    Sistema de regras If-This-Then-That (IFTTT).
    """

    # Tolerância para o laço de 60s derivar alguns segundos sem perder o
    # disparo. Maior que isso é horário vencido e não deve ser reenviado
    # atrasado depois de um restart.
    CATCHUP_WINDOW_MINUTES = 5

    def __init__(self, application):
        self.app = application
        self.rules = self._load_rules()
        self.running = False
        self._last_fired_day: Dict[str, str] = {}

    def _load_rules(self) -> List[Dict[str, Any]]:
        """
        Carrega regras de automação.
        Futuramente podem vir do banco de dados.
        """
        try:
            from jarvis.services.automation_helpers import _build_unknown_device_markup
        except ImportError:
            _build_unknown_device_markup = None

        return [
            {
                "id": "auto_1",
                "name": "Alerta Internet Down",
                "enabled": False,
                "trigger": {
                    "type": "event",
                    "event_type": "network.status_changed",
                    "condition": lambda e: e.payload.get("status") == "down"
                },
                "action": {
                    "type": "notify",
                    "message": "🚨 **ALERTA:** Internet caiu!\n\nVerificando conexão..."
                }
            },
            {
                "id": "auto_2",
                "name": "Modo Noturno",
                "enabled": False,
                "trigger": {
                    "type": "time",
                    "time": "22:00"
                },
                "action": {
                    "type": "notify",
                    "message": "🌙 Modo noturno ativado. Lembretes silenciados até 8h."
                }
            },
            {
                "id": "auto_3",
                "name": "Bom Dia Automático",
                "enabled": False,
                "trigger": {
                    "type": "time",
                    "time": "07:00"
                },
                "action": {
                    "type": "notify",
                    "message": "☀️ Bom dia! Que tal começar com um copo d'água?"
                }
            },
            {
                "id": "auto_4",
                "name": "Dispositivo Desconhecido",
                "enabled": False,
                "trigger": {
                    "type": "event",
                    "event_type": "network.unknown_device",
                    "condition": lambda e: True
                },
                "action": {
                    "type": "notify",
                    "message": lambda e: (
                        f"🕵️‍♂️ **Novo Dispositivo na Rede**\n\n"
                        f"IP: `{e.payload.get('ip')}`\n"
                        f"MAC: `{e.payload.get('mac')}`\n"
                        f"Vendor: `{e.payload.get('vendor', 'Desconhecido')}`\n\n"
                        f"O que deseja fazer?"
                    ),
                    "reply_markup": _build_unknown_device_markup
                }
            },
            {
                "id": "auto_5",
                "name": "Meta de Hidratação Perdida",
                "enabled": False,
                "trigger": {
                    "type": "event",
                    "event_type": "hydration.goal_missed",
                    "condition": lambda e: True
                },
                "action": {
                    "type": "notify",
                    "message": "💧 Ops! Não bateu a meta de água hoje. Amanhã a gente recupera!"
                }
            }
        ]

    async def start(self):
        """Inicia o motor de automações"""
        self.running = True
        logger.info("🤖 AutomationEngine iniciado.")

        # Loop de verificação de automações baseadas em tempo
        while self.running:
            try:
                await self._check_time_triggers()
            except Exception:
                logger.exception("Erro no loop de automações")
            # O sleep fica fora do try: dentro dele, uma falha persistente
            # reentrava no laço sem pausa e consumia a CPU do Pi.
            await asyncio.sleep(60)  # Verifica a cada minuto

    async def _check_time_triggers(self):
        """Verifica automações baseadas em horário"""
        now = datetime.now(Config.TZ)

        for rule in self.rules:
            if not rule["enabled"]:
                continue

            if rule["trigger"]["type"] != "time":
                continue

            if self._time_trigger_is_due(rule, now):
                self._last_fired_day[rule["id"]] = now.strftime("%Y-%m-%d")
                await self._execute_action(rule["action"], None)

    def _time_trigger_is_due(self, rule: Dict[str, Any], now: datetime) -> bool:
        """Dispara uma única vez por dia, assim que o horário é alcançado."""
        raw_time = rule["trigger"].get("time")
        try:
            hour, minute = (int(part) for part in str(raw_time).split(":"))
        except (ValueError, TypeError):
            logger.warning("Regra %s tem horário inválido: %r", rule.get("id"), raw_time)
            return False

        return schedule_is_due(
            now,
            hour,
            minute,
            period_key=now.strftime("%Y-%m-%d"),
            last_fired=self._last_fired_day.get(rule["id"]),
            window_minutes=self.CATCHUP_WINDOW_MINUTES,
        )

    async def on_event(self, event: Event):
        """
        Chamado quando um evento do sistema ocorre.
        Verifica se alguma regra se aplica.
        """
        for rule in self.rules:
            if not rule["enabled"]:
                continue

            trigger = rule["trigger"]
            if trigger["type"] == "event" and trigger["event_type"] == event.type:
                # Verifica condição (se houver)
                condition = trigger.get("condition")
                if condition and not condition(event):
                    continue

                # Executa ação
                await self._execute_action(rule["action"], event)

    async def _execute_action(self, action: Dict[str, Any], event: Event = None):
        """Executa uma ação de automação"""
        action_type = action["type"]

        if action_type == "notify":
            message = action["message"]
            reply_markup = action.get("reply_markup")

            # Se mensagem é callable, executa com evento
            if callable(message):
                message = message(event)

            # Se reply_markup é callable, executa com evento
            if callable(reply_markup):
                reply_markup = reply_markup(event)

            # Envia para todos os usuários permitidos
            from jarvis.config import Config
            chat_id = Config.ALLOWED_USER_ID

            try:
                await safe_send_message(
                    self.app.bot, chat_id, message, reply_markup=reply_markup
                )
                logger.info(f"Automação executada: {message[:50]}...")
            except Exception as e:
                logger.error(f"Erro ao enviar notificação de automação: {e}")
