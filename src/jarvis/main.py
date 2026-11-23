import logging
import asyncio
import os
import re
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from jarvis.config import Config

from jarvis.core.executor import Executor
from jarvis.core import router
from jarvis.database.persistence import Persistence
from jarvis.core.events import Event
from jarvis.core.telegram_safe import safe_edit_text, safe_reply_text

from jarvis.services.collector import CollectorService
from jarvis.services.scheduler import SchedulerService
from jarvis.services.guardian import GuardianService

import uvicorn

# =====================================================
# LOGGING — SANITIZAÇÃO DE SEGREDOS
# =====================================================
_SANITIZE_PATTERNS = []
_SENSITIVE_ENV_NAMES = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "PASS", "KEY")


def _add_secret_pattern(value, label):
    if value and isinstance(value, str) and len(value) >= 6:
        _SANITIZE_PATTERNS.append((re.escape(value), label))


token = getattr(Config, "TELEGRAM_TOKEN", None)
_add_secret_pattern(token, "TELEGRAM_TOKEN")
for env_name, env_value in os.environ.items():
    upper_name = env_name.upper()
    if any(marker in upper_name for marker in _SENSITIVE_ENV_NAMES):
        _add_secret_pattern(env_value, upper_name)

_TELEGRAM_BOT_URL_RE = re.compile(r"bot\d+:[^/\s\"']+")


def sanitize_log_text(value: str) -> str:
    if not isinstance(value, str):
        return value
    value = _TELEGRAM_BOT_URL_RE.sub("bot***TELEGRAM_TOKEN***", value)
    for pattern, replacement in _SANITIZE_PATTERNS:
        value = re.sub(pattern, f"***{replacement}***", value)
    return value


class SecretSanitizingFormatter(logging.Formatter):
    def format(self, record):
        return sanitize_log_text(super().format(record))


_old_log_record_factory = logging.getLogRecordFactory()


def _sanitizing_record_factory(*args, **kwargs):
    record = _old_log_record_factory(*args, **kwargs)
    if isinstance(record.msg, str):
        record.msg = sanitize_log_text(record.msg)
    if record.args:
        record.args = tuple(sanitize_log_text(arg) if isinstance(arg, str) else arg for arg in record.args)
    return record


logging.setLogRecordFactory(_sanitizing_record_factory)

class SecretSanitizer(logging.Filter):
    """Mascara tokens e chaves de API em todas as mensagens de log."""
    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = sanitize_log_text(record.msg)
        if hasattr(record, "args") and record.args:
            sanitized = []
            for arg in record.args:
                if isinstance(arg, str):
                    arg = sanitize_log_text(arg)
                sanitized.append(arg)
            record.args = tuple(sanitized)
        return True

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
for handler in logging.root.handlers:
    handler.addFilter(SecretSanitizer())
    handler.setFormatter(SecretSanitizingFormatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
# Also sanitize httpx/urllib3 (Telegram API logs the full URL)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("ROD")

# =====================================================
# COMMANDS
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ALLOWED_USER_ID:
        logger.warning("/start bloqueado (usuário não autorizado)")
        return

    await safe_reply_text(
        update.message,
        "ROD do Cerrado online.\n"
        "Guardião da casa ativado.\n\n"
        "Fala o trem aí 👊",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    executor: Executor = context.application.bot_data["executor"]
    response = await executor.execute({"intent": "help", "action": "show", "params": {}}, chat_id)
    if isinstance(response, dict):
        await safe_reply_text(
            update.message,
            response.get("text", ""),
            reply_markup=response.get("reply_markup"),
        )
    else:
        await safe_reply_text(update.message, response)


# =====================================================
# MESSAGE HANDLER
# =====================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Segurança básica
    if update.effective_user.id != Config.ALLOWED_USER_ID:
        logger.warning("Mensagem bloqueada (usuário não autorizado)")
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    logger.info(f"[MSG] {text}")

    await context.bot.send_chat_action(chat_id, "typing")

    try:
        # Roteamento Centralizado (Regras -> NLP -> IA)
        intent = await router.route(text, chat_id)

        executor: Executor = context.application.bot_data["executor"]
        response = await executor.execute(intent, chat_id)

        # Se a resposta for explicitamente None, o Executor já tratou (enviou mensagem direta)
        if response is None:
            return

        if not response:
            logger.warning(f"Executor returned empty response for intent: {intent}")
            response = "🤖 Não entendi direito ainda, uai. (Debug: Resposta vazia do executor)"

        if isinstance(response, dict):
            await safe_reply_text(
                update.message,
                response.get("text", ""),
                reply_markup=response.get("reply_markup"),
            )
        else:
            await safe_reply_text(update.message, response)

    except Exception as e:
        logger.exception("Erro crítico no handle_message")
        error_msg = f"❌ Deu ruim aqui.\n\n{str(e)}"
        await safe_reply_text(update.message, error_msg)


# =====================================================
# CALLBACK HANDLER (INTERATIVIDADE)
# =====================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Segurança básica (callback também precisa)
    if update.effective_user.id != Config.ALLOWED_USER_ID:
        return

    text = query.data
    chat_id = update.effective_chat.id

    logger.info(f"[CALLBACK] {text}")

    # Detecta callbacks de lembretes
    if text.startswith("rem_"):
        from jarvis.core.reminder_callbacks import ReminderCallbacks
        try:
            parts = text.split("_")
            if len(parts) < 3:
                logger.warning(f"Callback rem_ malformatado: {text}")
                await safe_reply_text(query.message, "❌ Callback inválido.")
                return
            action = parts[1]
            task_id = int(parts[2])

            if action == "done":
                await ReminderCallbacks.handle_done(task_id, chat_id, query)
                return

            elif action == "snooze":
                if len(parts) < 4:
                    await safe_reply_text(query.message, "❌ Callback snooze inválido (sem minutos).")
                    return
                minutes = int(parts[3])
                await ReminderCallbacks.handle_snooze(task_id, chat_id, minutes, query)
                return

            elif action == "cancel":
                await ReminderCallbacks.handle_cancel(task_id, chat_id, query)
                return

            elif action == "reschedule":
                await ReminderCallbacks.handle_reschedule_request(task_id, chat_id, query)
                return
            else:
                logger.warning(f"Ação de callback desconhecida: {action}")
                await safe_reply_text(query.message, "❌ Ação de callback desconhecida.")
                return
        except (ValueError, IndexError) as e:
            logger.warning(f"Erro ao parsear callback rem_: {e}")
            await safe_reply_text(query.message, "❌ Erro ao processar callback.")
            return

    # Detecta snooze de hidratação
    if text == "agora nao":
        from jarvis.modules.hydration import HydrationModule
        await HydrationModule.snooze_hydration(chat_id, query, minutes=15)
        return

    # Detecta callbacks de rede
    if text.startswith("net_"):
        executor: Executor = context.application.bot_data["executor"]
        await executor.handle_network_callback(chat_id, text, query)
        return

    if text == "reminder_delete_menu":
        await safe_reply_text(query.message, "Qual lembrete quer apagar? Use: cancelar lembrete 1\n\nDica: toque em 'listar lembretes' para ver os números.")
        return

    try:
        # Processa como se fosse texto (simula comando)
        intent = await router.route(text, chat_id)
        executor: Executor = context.application.bot_data["executor"]
        response = await executor.execute(intent, chat_id)

        # Se a resposta for explicitamente None, o Executor já tratou
        if response is None:
            return

        if not response:
             logger.warning(f"Executor callback returned empty response for: {text}")
             response = "🤖 Não entendi direito ainda, uai. (Debug: Callback vazio)"

        # Menus navegam na mesma mensagem para não poluir a conversa.
        if isinstance(response, dict):
             try:
                  await safe_edit_text(
                       query.edit_message_text,
                       response.get("text", ""),
                       reply_markup=response.get("reply_markup"),
                  )
             except BadRequest:
                  await safe_reply_text(
                       query.message,
                       response.get("text", ""),
                       reply_markup=response.get("reply_markup"),
                  )
        else:
             try:
                  await safe_edit_text(query.edit_message_text, response)
             except BadRequest:
                  await safe_reply_text(query.message, response)

    except Exception as e:
        logger.exception("Erro crítico no handle_callback")
        error_msg = f"❌ Deu ruim aqui.\n\n{str(e)}"
        await safe_reply_text(query.message, error_msg)


# =====================================================
# POST INIT (BOOTSTRAP REAL)
# =====================================================
async def post_init(application):
    """
    Executado após o bot subir.
    Aqui nasce o ROD de verdade.
    """

    logger.info("🚀 Inicialização pós-start iniciada")

    try:
        await application.bot.set_my_name("ROD")
        await application.bot.set_my_description(
            "Assistente residencial da RDP Studio: rede, Raspberry Pi, lembretes e contas."
        )
        await application.bot.set_my_short_description("ROD // RDP Studio // Home Operations")
    except Exception:
        logger.warning("Não foi possível sincronizar o nome público do bot no Telegram", exc_info=True)

    # -------------------------
    # BANCO
    # -------------------------
    Persistence.init_db()

    Persistence.log_event(
        Event(
            type="system.startup",
            source="main",
            payload={"status": "online"},
        )
    )

    # -------------------------
    # COLETOR AUTOMÁTICO (PASSO 5)
    # -------------------------
    collector = CollectorService(interval_seconds=60)
    scheduler = SchedulerService(application, interval_seconds=30)

    application.bot_data["collector"] = collector
    application.bot_data["scheduler"] = scheduler

    if "tasks" not in application.bot_data:
        application.bot_data["tasks"] = []

    task_collector = asyncio.create_task(collector.start())
    task_scheduler = asyncio.create_task(scheduler.start())

    application.bot_data["tasks"].append(task_collector)
    application.bot_data["tasks"].append(task_scheduler)

    logger.info("📡 Collector e Scheduler iniciados")

    # -------------------------
    # AUTOMATION ENGINE (NOVO)
    # -------------------------
    from jarvis.services.automations import AutomationEngine

    automation = AutomationEngine(application)
    application.bot_data["automation"] = automation
    task_automation = asyncio.create_task(automation.start())
    application.bot_data["tasks"].append(task_automation)
    logger.info("🤖 AutomationEngine iniciado")

    # -------------------------
    # GUARDIAN SERVICE (NOVO)
    # -------------------------
    if Config.ALLOWED_USER_ID:
        guardian = GuardianService(application, chat_id=Config.ALLOWED_USER_ID)
        application.bot_data["guardian"] = guardian
        task_guardian = asyncio.create_task(guardian.start())
        application.bot_data["tasks"].append(task_guardian)
        logger.info("🛡️ GuardianService iniciado")
    else:
        logger.warning("⚠️ ALLOWED_USER_ID não definido. GuardianService não foi iniciado.")

    # -------------------------
    # REPORTER SERVICE (DIARIO / SEMANAL)
    # -------------------------
    from jarvis.services.reporter import ReporterService
    reporter = ReporterService(application)
    application.bot_data["reporter"] = reporter
    task_reporter = asyncio.create_task(reporter.start())
    application.bot_data["tasks"].append(task_reporter)
    logger.info("ReporterService iniciado")

    # -------------------------
    # WEB DASHBOARD API
    # -------------------------
    from jarvis.api.app import app as fastapi_app

    # Inject telegram bot services into FastAPI state
    fastapi_app.state.bot_app = application
    fastapi_app.state.automation_service = application.bot_data.get("automation")

    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    application.bot_data["api_task"] = asyncio.create_task(server.serve())
    logger.info("🌐 Web Dashboard API inicializado")

# =====================================================
# MAIN
# =====================================================
def main():
    if not Config.TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN não configurado")

    application = (
        ApplicationBuilder()
        .token(Config.TELEGRAM_TOKEN)
        .post_init(post_init)


        .build()
    )

    # Executor = cérebro operacional
    application.bot_data["executor"] = Executor(application)

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("🟢 ROD do Cerrado operacional.")
    application.run_polling()


if __name__ == "__main__":
    main()
