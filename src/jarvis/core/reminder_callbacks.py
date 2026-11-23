import logging

from jarvis.core.telegram_safe import safe_edit_text, safe_send_message
from datetime import datetime, timedelta, timezone
from jarvis.database.persistence import Persistence
from jarvis.core.context import ContextEngine

logger = logging.getLogger("core.reminder_callbacks")

class ReminderCallbacks:
    """Handlers para callbacks de lembretes"""

    @staticmethod
    def _get_owned_task(task_id: int, chat_id: int):
        task = Persistence.get_task(task_id)
        if not task:
            return None, "Não encontrei esse lembrete. Talvez ele já tenha sido removido."
        if task.get("chat_id") != chat_id:
            logger.warning(f"Callback de lembrete negado: task_id={task_id}, chat_id={chat_id}")
            return None, "Esse lembrete não pertence a este chat."
        return task, None

    @staticmethod
    async def _finish_callback(query, text: str):
        if query and hasattr(query, "edit_message_text"):
            try:
                await safe_edit_text(query.edit_message_text, text)
                return
            except Exception as e:
                logger.warning(f"Falha ao editar mensagem de lembrete: {e}")
        if query and getattr(query, "message", None) and hasattr(query.message, "edit_text"):
            try:
                await safe_edit_text(query.message.edit_text, text)
                return
            except Exception as e:
                logger.warning(f"Falha ao editar mensagem original de lembrete: {e}")
        if query and getattr(query, "bot", None):
            await safe_send_message(query.bot, query.message.chat_id, text)

    @staticmethod
    async def handle_done(task_id: int, chat_id: int, query):
        """Marca lembrete como concluído"""
        task, error = ReminderCallbacks._get_owned_task(task_id, chat_id)
        if error:
            await ReminderCallbacks._finish_callback(query, error)
            return

        if task.get("status") == "completed":
            await ReminderCallbacks._finish_callback(query, "Esse lembrete já estava concluído.")
            return

        Persistence.update_task_status(task_id, 'completed')
        Persistence.log_interaction(task_id, "done")

        await ReminderCallbacks._finish_callback(query, f"✅ Concluído: {task['text']}")

        logger.info(f"Lembrete {task_id} concluído pelo usuário")

    @staticmethod
    async def handle_snooze(task_id: int, chat_id: int, minutes: int, query):
        """Adia lembrete por X minutos"""
        task, error = ReminderCallbacks._get_owned_task(task_id, chat_id)
        if error:
            await ReminderCallbacks._finish_callback(query, error)
            return

        if task.get("status") == "cancelled":
            await ReminderCallbacks._finish_callback(query, "Esse lembrete já foi cancelado.")
            return

        new_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        Persistence.update_task_next_run_and_status(task_id, new_time, 'active')
        Persistence.log_interaction(task_id, "snooze", str(minutes))

        await ReminderCallbacks._finish_callback(query, f"⏰ Adiado por {minutes} minutos: {task['text']}")

        logger.info(f"Lembrete {task_id} adiado por {minutes}min")

    @staticmethod
    async def handle_cancel(task_id: int, chat_id: int, query):
        """Cancela lembrete definitivamente"""
        task, error = ReminderCallbacks._get_owned_task(task_id, chat_id)
        if error:
            await ReminderCallbacks._finish_callback(query, error)
            return

        if task.get("status") == "cancelled":
            await ReminderCallbacks._finish_callback(query, "Esse lembrete já estava cancelado.")
            return

        Persistence.update_task_status(task_id, 'cancelled')
        Persistence.log_interaction(task_id, "cancel")

        await ReminderCallbacks._finish_callback(query, f"🗑️ Cancelado: {task['text']}")

        logger.info(f"Lembrete {task_id} cancelado pelo usuário")

    @staticmethod
    async def handle_reschedule_request(task_id: int, chat_id: int, query):
        """Inicia fluxo para remarcar lembrete."""
        task, error = ReminderCallbacks._get_owned_task(task_id, chat_id)
        if error:
            await ReminderCallbacks._finish_callback(query, error)
            return

        ContextEngine.save_context(chat_id, {
            "flow": {
                "type": "reminder_reschedule",
                "step": "awaiting_time",
                "data": {"task_id": task_id},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        })
        await ReminderCallbacks._finish_callback(
            query,
            f"📅 Pra quando eu remarco *{task['text']}*?\n\nEx: `hoje às 20h`, `amanhã às 9h` ou `daqui 30 minutos`."
        )
