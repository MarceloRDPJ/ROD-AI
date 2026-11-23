import html
import inspect
from functools import partial
import logging
import re
from typing import Dict, Any, List

from jarvis.database.persistence import Persistence
from jarvis.core.events import Event
from jarvis.core.context import ContextEngine
from jarvis.core.context_reader import ContextReader
from jarvis.core import menus
from jarvis.core.flows import RemindersFlow
from jarvis.core.personality import Personality

from jarvis.modules.system import SystemModule
from jarvis.modules.network import NetworkModule
from jarvis.modules.hydration import HydrationModule
from jarvis.modules.adguard import AdGuardClient
from jarvis.services.device_intelligence import DeviceIntelligence
from jarvis.core.telegram_safe import (
    DEFAULT_PARSE_MODE,
    safe_edit_text,
    safe_send_message,
)
from datetime import datetime
from jarvis.config import Config
import os
import asyncio
import time

logger = logging.getLogger("core.executor")

class Executor:
    """
    Executor do ROD do Cerrado — EXECUÇÃO CONTROLADA
    """

    SENSITIVE_ACTIONS = {
        "network_block",
        "network_unblock",
        "network_block_device",
        "network_block_site"
    }

    def __init__(self, application):
        self.app = application
        Persistence.init_db()
        logger.info("Executor inicializado com sucesso.")

    async def execute(self, intent_data: Dict[str, Any], chat_id: int) -> str:
        # ===== VALIDAÇÃO DE SEGURANÇA - ADICIONAR AQUI =====

        # Valida que apenas usuário autorizado pode executar comandos
        if chat_id != Config.ALLOWED_USER_ID:
            logger.warning(f"🚨 Tentativa de acesso não autorizado: chat_id={chat_id}")
            return "🚫 Acesso negado. Você não está autorizado a usar este bot."

        # ===== FIM DA VALIDAÇÃO =====

        if not isinstance(intent_data, dict):
            return "❌ Comando inválido."

        intent: str = intent_data.get("intent")
        action: str = intent_data.get("action", "default")
        params: Dict[str, Any] = intent_data.get("params", {})
        requires_confirmation: bool = intent_data.get("requires_confirmation", False)

        logger.info(f"Executor → intent={intent} | action={action}")

        # Log & Context
        try:
            Persistence.log_event(Event(type=f"{intent}.{action}", source="executor", payload=intent_data))
            ContextEngine.save_context(chat_id, intent_data)
        except Exception:
            logger.exception("Erro ao registrar evento/contexto")

        # Confirmation
        if intent == "action_confirm": return await self._confirm_action(chat_id)
        if intent == "action_cancel": return self._cancel_action(chat_id)

        # Enforce Confirmation for Sensitive Actions
        if intent in self.SENSITIVE_ACTIONS:
            requires_confirmation = True

        if requires_confirmation:
            validation_error = self._validate_sensitive_action(intent, params)
            if validation_error:
                return validation_error
            ContextEngine.set_pending_action(chat_id, intent_data)
            return self._confirmation_prompt(intent, params)

        if intent == "chat" and intent_data.get("source") == "local_clarification":
            return self._clarification_prompt(params.get("response", Personality.get_response("FALLBACK")))

        return await self._execute_intent(intent, action, params, chat_id)

    async def _execute_intent(self, intent: str, action: str, params: Dict[str, Any], chat_id: int) -> str:
        # ---------------- COMMAND LIST (NEW) ----------------
        # Menus sao apresentacao: vivem em core/menus.py. Antes o
        # command_list despejava o catalogo inteiro num texto so, e cada
        # menu repetia a montagem do teclado aqui dentro.
        menu = menus.build(intent)
        if menu:
            return menu

        # ---------------- NETWORK SCAN (UX Aprimorada) ----------------
        if intent == "network_scan":
            # 1. Send Initial Status Message
            status_msg = await self.app.bot.send_message(
                chat_id=chat_id,
                text="⏳ Iniciando varredura profunda da rede...",
                parse_mode=DEFAULT_PARSE_MODE,
            )

            # 2. Callback for Updates
            last_text = ""
            async def update_status(text):
                nonlocal last_text
                if text != last_text:
                    try:
                        await safe_edit_text(
                            partial(
                                self.app.bot.edit_message_text,
                                chat_id=chat_id,
                                message_id=status_msg.message_id,
                            ),
                            text,
                        )
                        last_text = text
                    except Exception as e:
                        logger.warning(f"Failed to update status: {e}")

            # 3. Run Deep Scan
            try:
                devices = await NetworkModule.scan_network_deep(status_callback=update_status)

                # 4. Format Final Report
                if not devices:
                    final_text = "⚠️ Nenhum dispositivo encontrado."
                else:
                    final_text = f"🕵️‍♂️ *Relatório de Rede ({len(devices)} dispositivos):*\n\n"

                    for d in devices:
                        ip = d['ip']
                        mac = d['mac']
                        vendor = d['vendor']
                        name = d['custom_name']
                        hostname = d['hostname']
                        guess = d['guessed_type']

                        # Icon Logic
                        icon = "🖥️"
                        desc = vendor

                        if "Apple" in guess: icon = "🍎"
                        elif "Linux" in guess: icon = "🐧"
                        elif "Windows" in guess: icon = "🪟"
                        elif "IoT" in guess: icon = "🔌"
                        elif "Raspberry" in guess: icon = "🍓"

                        # Name Priority: Custom > Hostname > Vendor
                        display_name = name if name else (hostname if hostname else vendor)

                        # Extra info line
                        extra = ""
                        if guess != "Dispositivo Desconhecido":
                            extra = f" _({guess})_"
                        elif hostname:
                            extra = f" _(Host: {hostname})_"

                        final_text += f"{icon} `{ip}` — *{display_name}*{extra}\n"

                # 5. Final Update (overwrite status message)
                try:
                    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                    keyboard = [[InlineKeyboardButton("🔄 Escanear Novamente", callback_data="quem ta na rede")]]
                    await safe_edit_text(
                        partial(
                            self.app.bot.edit_message_text,
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                        ),
                        final_text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    return None # Already sent response via edit
                except:
                    return final_text

            except Exception as e:
                logger.exception("Deep scan failed")
                return f"❌ Erro durante a varredura: {e}"

        # ---------------- FLOW INPUT ----------------
        if intent == "flow_input":
            ctx = ContextEngine.get_context(chat_id)
            flow = ctx.get("flow")
            text_input = params.get("text", "")
            if flow:
                # Trata fluxos de rede (Cadastro)
                if flow.get("type") == "network_register":
                    result = await self._handle_network_registration(chat_id, text_input, ctx)
                    if result: return result

                # Trata fluxos de hidratação (Setup ou Confirm)
                if flow.get("type") in ["hydration_confirm", "hydration_setup"]:
                    result = HydrationModule.handle_flow(chat_id, text_input, ctx)
                    if result: return result
                    st_response = Personality.get_small_talk(text_input)
                    if st_response: return st_response
                    return Personality.get_response("FALLBACK")

                if flow.get("type") == "reminder_reschedule":
                    return RemindersFlow.handle_reschedule_response(chat_id, text_input, ctx)
            return self._decorate_reminder_flow_response(
                chat_id,
                RemindersFlow.handle_response(chat_id, text_input, ctx),
            )

        # ---------------- STANDARD INTENTS ----------------
        if intent == "chat": return params.get("response", Personality.get_response("FALLBACK"))
        if intent == "small_talk": return Personality.get_small_talk(params.get("text", ""))

        # IDENTITY
        if intent == "identity_who":
            return Personality.get_response("IDENTITY_WHO")

        if intent == "identity_creator":
            return Personality.get_response("IDENTITY_CREATOR")

        if intent == "identity_purpose":
            return Personality.get_response("IDENTITY_PURPOSE")

        if intent == "identity_capabilities":
            return Personality.get_response("IDENTITY_CAPABILITIES")

        if intent == "identity_tech":
            return Personality.get_response("IDENTITY_TECH_STACK")

        if intent == "greet": return Personality.get_response("GREET")

        if intent == "system_status": return await SystemModule.get_status()

        # --- NEW HANDLERS FOR SUBMENU ITEMS ---
        if intent == "automation_list":
            automation = getattr(self.app, "bot_data", {}).get("automation") if self.app else None
            if not automation:
                return "🤖 Automações\n\nMotor de automações não está disponível agora. Nenhuma automação confirmada."
            rules = getattr(automation, "rules", []) or []
            if not rules:
                return "🤖 Automações\n\nNenhuma regra carregada."
            lines = []
            for rule in rules:
                status = "ativa" if rule.get("enabled") else "pausada"
                trigger = rule.get("trigger", {})
                if trigger.get("type") == "time":
                    trigger_desc = f"horário {trigger.get('time')}"
                else:
                    trigger_desc = f"evento {trigger.get('event_type', 'desconhecido')}"
                lines.append(f"• {rule.get('name', rule.get('id'))}: {status} ({trigger_desc})")
            return "🤖 Automações carregadas\n\n" + "\n".join(lines) + "\n\nAções dependentes de integrações externas só executam se o serviço estiver configurado."

        if intent == "automation_config":
            return "⚙️ Configuração de Automações\n\nCriação/edição pelo Telegram ainda não está implementada. Hoje eu apenas listo e executo as regras locais carregadas no código/configuração."

        if intent == "system_logs":
            try:
                events = Persistence.get_recent_events(limit=5)
                if events:
                    lines = [f"• `{e['type']}` de `{e['source']}` em {e['timestamp'][:19]}" for e in events]
                    return "📜 **Logs do Sistema (Últimos Eventos)**\n\n" + "\n".join(lines)

                snapshots = Persistence.get_recent_snapshots(1440, limit=5)
                if snapshots:
                    lines = [f"• Snapshot {s['timestamp'][:19]}" for s in snapshots]
                    return "📜 **Snapshots Recentes (24h)**\n\n" + "\n".join(lines)

                return "📜 **Logs do Sistema**\n\nNenhum evento ou snapshot registrado."
            except Exception as e:
                return f"❌ Erro ao ler logs: {e}"

        # Removed old menu handlers that delegated to _build_menu

        if intent == "network_speed":
            await safe_send_message(
                self.app.bot, chat_id, "🚀 Medindo a conexão pelo Raspberry Pi..."
            )
            return await NetworkModule.run_speedtest()

        if intent == "network_status":
            return await NetworkModule.check_ping()

        if intent == "network_device_details":
            ip = params.get("ip") or params.get("target")
            mac = await NetworkModule.resolve_mac_by_ip(ip) if ip else None
            if not mac:
                return f"Nao encontrei o dispositivo {ip or ''} na rede agora."
            dossier = await DeviceIntelligence.dossier(ip, mac)
            return DeviceIntelligence.format_dossier(dossier)

        if intent == "network_rename":
            target = params.get("target")
            new_name = params.get("name")
            mac = await NetworkModule.resolve_mac_by_ip(target)
            if mac and new_name:
                Persistence.set_device_name(mac, new_name)
                return f"✅ Dispositivo {target} agora é conhecido como *{new_name}*."
            elif not mac: return f"❌ Não encontrei o IP {target} na rede agora."
            else: return "❌ Preciso do IP e do novo nome. Ex: mudar nome do 192.168.1.5 para TV Sala"

        if intent == "network_block_device":
            ip = params.get("ip") or params.get("target")
            if not ip:
                return "❌ Preciso do IP. Ex: bloquear 192.168.0.15"

            result = await AdGuardClient.block_client(ip)
            if result["success"]:
                return f"🚫 Dispositivo {ip} bloqueado no AdGuard."
            else:
                return f"❌ Erro ao bloquear: {result['message']}"

        if intent == "network_block_site":
            site = params.get("site") or params.get("domain")
            if not site:
                return "❌ Qual site? Ex: bloquear youtube.com"

            result = await AdGuardClient.block_domain(site, name=f"Bloqueio {site}")
            if result["success"]:
                return f"🚫 Site {site} bloqueado."
            else:
                return f"❌ Erro: {result['message']}"

        if intent == "network_stats":
            stats = await AdGuardClient.get_stats()
            top = await AdGuardClient.get_top_clients(limit=5)

            msg = f"📊 **Estatísticas de Rede**\n\n"
            msg += f"DNS Queries: {stats.get('num_dns_queries', 0)}\n"
            msg += f"Bloqueados: {stats.get('num_blocked_filtering', 0)}\n\n"
            msg += f"**Top 5 Consumidores:**\n"

            for client in top:
                msg += f"• {client['name'] or client['ip']}: {client['queries']} queries\n"

            return msg

        # Wake-on-LAN
        if intent == "wake_pc":
            if not Config.PC_MAC:
                return "❌ Wake-on-LAN ainda não está configurado no Pi."
            # Confirmação para ação sensível
            if not params.get("confirmed"):
                ContextEngine.set_pending_action(chat_id, {
                    "intent": "wake_pc",
                    "params": {"confirmed": True}
                })
                return (
                    "🖥️ *Wake-on-LAN*\n\n"
                    "Vou enviar pacote mágico para ligar o PC.\n\n"
                    "Confirma? Digite *confirmar* ou *cancelar*."
                )

            # Executa Wake-on-LAN
            try:
                result = await NetworkModule.wake_on_lan(Config.PC_MAC)
                if not result.get("success"):
                    return f"❌ Erro ao enviar pacote WOL: {result.get('message', 'falha desconhecida')}"

                return (
                    "🖥️ *Pacote WOL Enviado!*\n\n"
                    "Pacote mágico enviado para o PC configurado.\n\n"
                    "O PC deve ligar em alguns segundos.\n"
                    "Aguarde 30-60 segundos e verifique se está online."
                )

            except Exception:
                logger.exception("Erro ao executar Wake-on-LAN")
                return "❌ Erro ao enviar o pacote Wake-on-LAN."

        # Status do PC
        if intent == "pc_status":
            if not Config.PC_IP:
                return "❌ IP do PC ainda não está configurado no Pi."
            online = await NetworkModule.check_device_online(Config.PC_IP)

            if online:
                return "🟢 PC está ONLINE."
            else:
                return "🔴 PC está OFFLINE ou não respondendo."

        if intent == "context_query":
            try: return f"📊 Resultado técnico:\n```{ContextReader.handle(params)}```"
            except: return "❌ Erro ao analisar histórico."

        if intent == "reminder_set":
            if action == "create_request":
                return self._decorate_reminder_flow_response(
                    chat_id,
                    RemindersFlow.start_flow(chat_id, params),
                )
            return "Modo de criação direta descontinuado. Use fluxo interativo."

        if intent == "reminder_list":
            text = RemindersFlow.list_reminders(chat_id)
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [[InlineKeyboardButton("➕ Novo Lembrete", callback_data="criar lembrete"), InlineKeyboardButton("🗑️ Apagar Lembrete", callback_data="reminder_delete_menu")]]
                return {"text": text, "reply_markup": InlineKeyboardMarkup(keyboard)}
            except: return text

        if intent == "reminder_today":
            return RemindersFlow.list_today(chat_id)

        if intent == "reminder_overdue":
            return RemindersFlow.list_overdue(chat_id)

        if intent == "reminder_delete":
            index = params.get("index") or params.get("target_id")
            if index: return RemindersFlow.delete_reminder(chat_id, int(index))
            else: return "❌ Preciso do número do lembrete. Tenta 'listar lembretes' pra ver os números."

        if intent == "reminder_update":
            index = params.get("index")
            modification = params.get("modification")
            if index: return RemindersFlow.update_reminder(chat_id, int(index), modification)
            else:
                reminders = RemindersFlow.list_reminders(chat_id)
                return (
                    f"Pra editar eu preciso do número do lembrete.\n\n"
                    f"{reminders}\n"
                    f"Exemplo: `editar lembrete 1 para hoje às 20h`"
                )

        if intent in ["hydration_log", "hydration_log_explicit"]:
            amount = params.get("amount")
            return HydrationModule.log_intake(chat_id, amount, manual=True, explicit=True)

        if intent == "hydration_log_implicit":
            return HydrationModule.log_intake(chat_id, None, manual=True, explicit=False)

        if intent == "hydration_analytics":
            return HydrationModule.get_analytics(chat_id)

        if intent == "hydration_activate": return HydrationModule.activate_flow(chat_id)
        if intent == "hydration_status": return HydrationModule.get_status_message(chat_id)
        if intent == "hydration_control": return HydrationModule.control_hydration(chat_id, params.get("command", ""))
        if intent == "hydration_update": return HydrationModule.update_config(chat_id, params)
        if intent == "automation_create": return "🤖 Ainda não consigo criar automações novas pelo chat com segurança. Posso listar as regras carregadas e executar as existentes."

        if intent == "token_usage":
            return await Executor._get_token_usage_report()

        if intent == "daily_report":
            return await Executor._get_daily_report()

        if intent == "unknown_queries":
            return Executor._get_unknown_queries()

        logger.warning(f"Intent não tratada pelo Executor: {intent}")
        return "🤖 Ainda não sei executar isso… mas já anotei."

    @staticmethod
    async def _get_token_usage_report() -> str:
        from jarvis.database.persistence import Persistence
        today = Persistence.get_token_usage_today()
        all_time = Persistence.get_token_usage_all_time()

        msg = "📊 *Consumo de IA*\n\n"
        msg += f"*Hoje:*\n"
        msg += f"• Chamadas: {today['calls']}\n"
        msg += f"• Tokens: {today['total']} ({today['prompt']} in / {today['completion']} out)\n"
        msg += f"• Custo: ${today['cost']:.6f}\n\n"
        msg += f"*Total (todo histórico):*\n"
        msg += f"• Chamadas: {all_time['calls']}\n"
        msg += f"• Tokens: {all_time['total']}\n"
        msg += f"• Custo: ${all_time['cost']:.6f}\n\n"

        if today['calls'] == 0:
            msg += "_Nenhuma chamada de API hoje. O ROD resolveu tudo localmente/gratuito._ 🤖"
        else:
            msg += f"_Custo médio por chamada: ${today['cost']/max(today['calls'],1):.8f}_"

        return msg

    @staticmethod
    async def _get_daily_report() -> str:
        from jarvis.database.persistence import Persistence
        from jarvis.modules.network import NetworkModule
        from jarvis.modules.system import SystemModule
        import os

        # Token usage
        tokens = Persistence.get_token_usage_today()
        unknown = Persistence.get_unknown_queries_today()
        errors = Persistence.get_api_errors_today()

        # System status
        try:
            raw = await SystemModule.get_raw_status()
            temp = f"{raw['temperature_c']}C" if raw.get('temperature_c') else "N/A"
            uptime = str(__import__('datetime').timedelta(seconds=raw['uptime_seconds']))
            sys_info = f"CPU: {raw['cpu_percent']}% | RAM: {raw['memory']['percent']}% | Temp: {temp}"
        except:
            sys_info = "N/A"

        # Internet
        try:
            ping = await NetworkModule.get_ping_metrics()
            net = "Online" if ping.get('success') else "Offline"
            lat = ping.get('latency_ms', 'N/A')
            net_info = f"{net} ({lat}ms)"
        except:
            net_info = "N/A"

        msg = "📋 *Relatório Diário — ROD do Cerrado*\n\n"
        msg += f"🖥️ *Sistema*\n{sys_info}\nUptime: {uptime}\n\n"
        msg += f"🌐 *Internet*\n{net_info}\n\n"
        msg += f"🤖 *IA Local / Gratuita*\n"
        msg += f"• {tokens['calls']} chamadas · {tokens['total']} tokens\n"
        msg += f"• Custo: ${tokens['cost']:.6f}\n\n"

        if unknown:
            msg += f"❓ *Consultas não reconhecidas:* {len(unknown)}\n"
            for q in unknown[:5]:
                msg += f"• _{q['query'][:50]}_\n"
            msg += "\n"

        if errors:
            msg += f"⚠️ *Erros de API:* {len(errors)}\n\n"
        else:
            msg += "✅ *Nenhum erro de API hoje.*\n\n"

        msg += "_Relatório 100% local — zero tokens gastos para gerar isso._"
        return msg

    @staticmethod
    def _get_unknown_queries() -> str:
        from jarvis.database.persistence import Persistence
        queries = Persistence.get_unknown_queries_today()
        total = Persistence.get_unknown_queries_count(days=30)

        if not queries:
            return "❓ Nenhuma consulta desconhecida hoje. Tô entendendo tudo! 🤖"

        msg = f"📝 *Consultas não reconhecidas (hoje: {len(queries)}, 30d: {total})*\n\n"
        for q in queries:
            msg += f"• ❓ {q['query'][:60]}\n"

        msg += "\n_Essas queries são registradas para eu aprender e melhorar._"
        return msg

    async def _confirm_action(self, chat_id: int) -> str:
        stored = ContextEngine.get_pending_action(chat_id)
        if not stored or not isinstance(stored.get("action"), dict):
            return "⚠️ Nenhuma ação pendente para confirmar."
        try:
            timestamp = datetime.fromisoformat(stored["timestamp"])
            now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
            if (now - timestamp).total_seconds() > 15 * 60:
                ContextEngine.clear_pending_action(chat_id)
                return "⏳ Essa confirmação expirou. Envie o comando novamente."
        except (KeyError, TypeError, ValueError):
            ContextEngine.clear_pending_action(chat_id)
            return "⚠️ A confirmação pendente estava inválida. Envie o comando novamente."

        pending = stored["action"]
        ContextEngine.clear_pending_action(chat_id)
        logger.info("Ação sensível confirmada: intent=%s", pending.get("intent"))
        return await self._execute_intent(
            pending.get("intent"),
            pending.get("action", "default"),
            pending.get("params", {}),
            chat_id,
        )

    def _cancel_action(self, chat_id: int) -> str:
        if ContextEngine.get_pending_action(chat_id):
            ContextEngine.clear_pending_action(chat_id)
            return "🛑 Ação cancelada com sucesso."
        return "⚠️ Nenhuma ação pendente para cancelar."

    @staticmethod
    def _validate_sensitive_action(intent: str, params: Dict[str, Any]) -> str | None:
        if intent == "network_block_device":
            ip = params.get("ip") or params.get("target")
            if not AdGuardClient._is_ip(ip):
                return "Não sei qual dispositivo bloquear. Envie o IP ou use o botão do alerta."
        if intent == "network_block_site":
            domain = params.get("site") or params.get("domain")
            if not AdGuardClient._is_domain(domain or ""):
                return "Não identifiquei um domínio válido. Exemplo: bloquear youtube.com"
        return None

    @staticmethod
    def _confirmation_prompt(intent: str, params: Dict[str, Any]) -> Dict[str, Any]:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if intent == "network_block_device":
            detail = f"Bloquear o dispositivo {params.get('ip') or params.get('target')} no AdGuard?"
        elif intent == "network_block_site":
            detail = f"Bloquear o site {params.get('site') or params.get('domain')} no AdGuard?"
        else:
            detail = "Executar esta ação sensível?"
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Confirmar", callback_data="confirmar"),
                InlineKeyboardButton("Cancelar", callback_data="cancelar"),
            ]]
        )
        return {"text": f"⚠️ {detail}\n\nA confirmação expira em 15 minutos.", "reply_markup": keyboard}

    @staticmethod
    def _clarification_prompt(message: str) -> Dict[str, Any]:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Sistema", callback_data="menu_sistema"),
                    InlineKeyboardButton("Rede", callback_data="menu_rede"),
                ],
                [
                    InlineKeyboardButton("Agenda", callback_data="menu_agenda"),
                    InlineKeyboardButton("Menu", callback_data="help"),
                ],
            ]
        )
        return {"text": message, "reply_markup": keyboard}

    @staticmethod
    def _decorate_reminder_flow_response(chat_id: int, response: Any) -> Any:
        flow = ContextEngine.get_context(chat_id).get("flow")
        if not isinstance(flow, dict) or flow.get("type") != "reminder_creation":
            return response
        if flow.get("step") != "confirmation" or not isinstance(response, str):
            return response

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Confirmar", callback_data="sim"),
                InlineKeyboardButton("Cancelar", callback_data="nao"),
            ]]
        )
        return {"text": response, "reply_markup": keyboard}

    async def handle_network_callback(self, chat_id: int, data: str, query):
        """
        Trata callbacks 'net_xxx' vindos de automações.
        """
        parts = data.split("_")
        action = parts[1] # reg, block, ignore

        if action == "ignore":
            await safe_edit_text(query.edit_message_text, "👁️ Dispositivo ignorado.")
            return

        if action == "info":
            ip = parts[2]
            mac = parts[3] if len(parts) > 3 else await NetworkModule.resolve_mac_by_ip(ip)
            if not mac:
                await safe_edit_text(query.edit_message_text, "Não consegui obter o MAC desse IP agora.")
                return
            dossier = await DeviceIntelligence.dossier(ip, mac)
            await safe_edit_text(query.edit_message_text, DeviceIntelligence.format_dossier(dossier))
            return

        if action == "block":
            ip = parts[2]
            ContextEngine.save_context(chat_id, {"flow": None})
            ContextEngine.set_network_target(chat_id, ip)
            ContextEngine.set_pending_action(chat_id, {
                "intent": "network_block_device",
                "action": "block",
                "params": {"ip": ip, "confirmed": True},
            })
            confirmation = self._confirmation_prompt("network_block_device", {"ip": ip})
            await safe_edit_text(
                query.edit_message_text,
                confirmation["text"],
                reply_markup=confirmation["reply_markup"],
            )
            return

        if action == "reg":
            # net_reg_{ip}_{mac}
            ip = parts[2]
            mac = parts[3] if len(parts) > 3 else None

            if not mac:
                 # Try resolve if missing (legacy compat)
                 mac = await NetworkModule.resolve_mac_by_ip(ip)

            if not mac:
                 await safe_edit_text(query.edit_message_text, "❌ Não consegui identificar o MAC address para cadastro.")
                 return

            # Start Flow
            ContextEngine.save_context(chat_id, {
                "flow": {
                    "type": "network_register",
                    "step": "ask_name",
                    "data": {"ip": ip, "mac": mac}
                }
            })

            await safe_edit_text(
                query.edit_message_text,
                f"📝 *Cadastro de Dispositivo*\nIP: `{ip}`\nMAC: `{mac}`\n\nQual nome você quer dar para ele?",
            )
            return

    async def _handle_network_registration(self, chat_id: int, text: str, ctx: Dict) -> str:
        flow = ctx.get("flow")
        data = flow.get("data")
        mac = data.get("mac")
        ip = data.get("ip")

        # Smart Extraction: Handle "renomear X para Y" inside flow
        name = text.strip()

        # Try to clean common prefixes if user repeats the command
        import re
        # Removes "renomear ip: 192.168.1.56 para" or similar
        match = re.search(r'(?:para|por|chamar de)\s+(.+)$', name, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
        else:
            # Clean "renomear X" if present but no preposition
            if "renomear" in name.lower():
                 # fallback, take last part? Dangerous. Just take as is if no preposition.
                 pass

        Persistence.set_device_name(mac, name)

        # Clear Flow
        ContextEngine.save_context(chat_id, {"flow": None})

        return f"✅ Dispositivo `{ip}` cadastrado como *{name}*."
