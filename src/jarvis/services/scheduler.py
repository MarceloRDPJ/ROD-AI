import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict
from jarvis.database.persistence import Persistence
from jarvis.modules.reminders import get_reminder_message
from jarvis.modules.hydration import HydrationModule
from jarvis.config import Config
from jarvis.core.telegram_safe import safe_send_message

logger = logging.getLogger("services.scheduler")


def _build_reminder_markup(task_id: int):
    try:
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = [
            [
                InlineKeyboardButton("✅ Feito", callback_data=f"rem_done_{task_id}"),
                InlineKeyboardButton("⏰ +10min", callback_data=f"rem_snooze_{task_id}_10"),
            ],
            [
                InlineKeyboardButton("⏰ +1h", callback_data=f"rem_snooze_{task_id}_60"),
                InlineKeyboardButton("📅 Remarcar", callback_data=f"rem_reschedule_{task_id}"),
            ],
            [
                InlineKeyboardButton("❌ Cancelar", callback_data=f"rem_cancel_{task_id}"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logger.warning(f"Botões de lembrete indisponíveis; enviando texto simples: {e}")
        return None

class SchedulerService:
    """
    SchedulerService — Coração do sistema de lembretes.

    Responsabilidades:
    - Loop infinito verificando tarefas vencidas (Persistence)
    - Execução de tarefas (Envio de msg via Telegram)
    - Reagendamento de tarefas recorrentes
    - Lógica de "Madrugada" (Silêncio)
    """

    def __init__(self, application, interval_seconds: int = None):
        self.app = application
        self.interval = interval_seconds or Config.SCHEDULER_INTERVAL_SECONDS
        self.running = False

    async def start(self):
        if self.running:
            return

        self.running = True
        logger.info("⏰ SchedulerService iniciado.")

        while self.running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("Erro no ciclo do Scheduler")

            await asyncio.sleep(self.interval)

    async def run_cycle(self):
        now = datetime.now(timezone.utc)

        # 1. Busca tarefas vencidas
        tasks = Persistence.get_active_tasks_due(now)

        if not tasks:
            return

        logger.info(f"Scheduler: {len(tasks)} tarefas para processar.")

        for task in tasks:
            await self.process_task(task, now)

    async def process_task(self, task: Dict, now: datetime):
        chat_id = task['chat_id']
        task_id = task['id']
        action = task.get('action')

        # === 1. HIDRATAÇÃO (Novo Sistema) ===
        if action in ['hydration', 'hydration_check']:
            # Delega TUDO para o módulo (Estado, Quiet Hours, Intervalo Dinâmico)
            await HydrationModule.check_schedule(self.app, task)

            # O Scheduler prossegue para o Reagendamento Padrão abaixo.
            # Se o módulo decidiu não enviar nada (Quiet Hours ou bebeu recente),
            # o Scheduler simplesmente agendará a próxima verificação para daqui a X minutos.
            # Isso mantém o "Heartbeat" vivo.

        else:
            # === 2. Envio da Mensagem (Genérico) ===
            message = get_reminder_message(task, now)
            try:
                await safe_send_message(
                    self.app.bot,
                    chat_id,
                    message,
                    reply_markup=_build_reminder_markup(task_id),
                    disable_notification=False,
                )
            except Exception as e:
                logger.error(f"Falha ao enviar lembrete {task_id}: {e}")
                return # Não reagenda em caso de falha de transporte

        # === 3. Reagendamento ===
        if task['type'] == 'recurring':
            interval = task['interval_minutes']
            base = datetime.fromisoformat(task['next_run'])
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            next_run = base + timedelta(minutes=interval)
            while next_run <= now:
                next_run += timedelta(minutes=interval)

            # Se a próxima execução cair no período de silêncio (hidratação),
            # já ajusta para o dia seguinte?
            # Não, deixa o check acima (Quiet Hours) lidar com isso na próxima execução.
            # Isso mantém a lógica simples.

            Persistence.update_task_next_run(task_id, next_run)
            logger.info(f"Tarefa {task_id} reagendada para {next_run}")
        else:
            meta = json.loads(task.get('meta') or '{}')
            if meta.get('nag', True):
                interval = int(meta.get('nag_interval_minutes') or 1)
                next_run = now + timedelta(minutes=interval)
                Persistence.update_task_next_run_and_status(task_id, next_run, 'active')
                Persistence.log_interaction(task_id, 'nag_sent', str(interval))
                logger.info(f"Tarefa {task_id} enviada e reagendada para cobrança em {interval}min.")
            else:
                Persistence.update_task_status(task_id, 'delivered')
                logger.info(f"Tarefa {task_id} entregue; aguardando ação do usuário.")

    def check_madrugada(self, now: datetime) -> bool:
        # Horário local baseado na configuração
        local_now = now.astimezone(Config.TZ)
        local_hour = local_now.hour
        return 23 <= local_hour or local_hour < 6
