import html
import inspect
import logging
import re
from typing import Dict, Any, List

from jarvis.database.persistence import Persistence
from jarvis.core.events import Event
from jarvis.core.context import ContextEngine
from jarvis.core.context_reader import ContextReader
from jarvis.core.flows import RemindersFlow
from jarvis.core.personality import Personality
from jarvis.core import bill_screen

from jarvis.modules.system import SystemModule
from jarvis.modules.network import NetworkModule
from jarvis.modules.hydration import HydrationModule
from jarvis.modules.adguard import AdGuardClient
from jarvis.services.equatorial_providers import (
    WEB_SESSION,
    EquatorialProviderChain,
    equatorial_code,
)
from datetime import datetime
from jarvis.config import Config
import os
import asyncio
import time

logger = logging.getLogger("core.executor")

# =====================================================
# CONTAS & FATURAS — CONTRATO COM O NÓ POCO
# =====================================================
# As ações de artefato pertencem à fila do nó Android. Ficam nomeadas aqui, num
# lugar só, para que ligar/desligar o contrato seja uma linha e não uma caçada
# pelo arquivo. Nenhuma delas paga, confirma ou movimenta qualquer valor.
POCO_PIX_ACTION = "get_equatorial_pix"
POCO_BOLETO_ACTION = "get_equatorial_boleto"

# Um artefato de pagamento só é reaproveitado dentro da MESMA fatura e por pouco
# tempo. Sem o limite, um toque em PIX no mês seguinte devolveria o código do mês
# anterior — erro caro e silencioso.
BILL_ARTIFACT_TTL_SECONDS = 900

# Imóveis que já tiveram uma leitura concluída. Continua sendo a prova histórica;
# versões novas do Poco também expõem somente os nomes lógicos configurados.
BILL_STATE_KEY = "bill_properties_confirmed"

# Mensagens de falha que o dono lê. Código tipado, nome de exceção e traceback
# ficam no log; na tela fica o que dá para fazer a respeito.
POCO_UNAVAILABLE_MESSAGE = "📱 O Poco está temporariamente indisponível. Tente novamente."
PORTAL_UNAVAILABLE_MESSAGE = "⚡ A Equatorial não respondeu agora. Tente novamente em alguns minutos."
HUMAN_CHECK_ALL_CHANNELS_MESSAGE = (
    "⚠️ A Equatorial exigiu verificação humana em todos os canais automáticos "
    "disponíveis. Nenhum pagamento foi realizado."
)
BILL_GENERIC_FAILURE_MESSAGE = (
    "Não consegui consultar a Equatorial agora. Tente novamente em alguns minutos."
)
SANEAGO_GENERIC_FAILURE_MESSAGE = (
    "Não consegui consultar a Saneago agora. Tente novamente em alguns minutos."
)
BILL_ACTION_UNAVAILABLE_MESSAGE = (
    "Essa opção ainda não está habilitada no Poco. A consulta continua funcionando e "
    "nenhum pagamento foi realizado."
)
BILL_ARTIFACT_UNAVAILABLE_MESSAGE = (
    "Não recebi o arquivo do Poco. Nenhum pagamento foi realizado; tente novamente em "
    "alguns minutos."
)
# A última leitura que eu tenho é antiga e está rotulada como antiga. Entregar o
# Pix ou o boleto guardado dela seria apresentar dado velho como cobrança de agora
# — o erro mais caro desta tela, porque quem paga não tem como perceber.
BILL_STALE_ONLY_MESSAGE = (
    "A última informação que tenho dessa conta é uma leitura guardada, não a fatura de "
    "agora. Toque em ATUALIZAR para eu buscar de novo; não vou entregar um código de "
    "pagamento antigo. Nenhum pagamento foi realizado."
)
PROVIDER_LABELS = {"equatorial": "Equatorial", "saneago": "Saneago"}


class Executor:
    """
    Executor do ROD do Cerrado — EXECUÇÃO CONTROLADA
    """

    SENSITIVE_ACTIONS = {
        "system_reboot",
        "system_shutdown",
        "system_restart_adguard",
        "network_block",
        "network_unblock",
        "network_block_device",
        "network_block_site"
    }

    def __init__(self, application):
        self.app = application
        Persistence.init_db()
        self.pending_actions: Dict[int, Dict[str, Any]] = {}
        # Single-flight de contas: uma automação por (concessionária, imóvel, ação).
        # O Poco executa um job por vez, então dois toques rápidos no mesmo botão
        # não criavam duas leituras — criavam uma fila que dobrava a espera.
        self._bill_flights: Dict[tuple, Any] = {}
        # Se o voo em curso nasceu de um ATUALIZAR do dono. Sem lembrar
        # disso, um toque forçado herdava o resultado de uma consulta que
        # não forçou nada.
        self._bill_flight_forced: Dict[tuple, bool] = {}
        # Artefato de pagamento em memória, nunca em disco e nunca em log.
        self._bill_artifacts: Dict[tuple, Dict[str, Any]] = {}
        # Referência da última leitura confirmada, para provar que o artefato
        # entregue é da mesma fatura que está na tela.
        self._bill_reference: Dict[tuple, str] = {}
        # (concessionária, imóvel) cuja informação mais recente é CACHE rotulado.
        # Enquanto estiver marcado, nenhum artefato de pagamento sai daqui.
        self._bill_stale_only: Dict[tuple, bool] = {}
        # Cadeia de canais oficiais da Equatorial. Uma instância por executor: a
        # memória de saúde só serve para algo se sobreviver entre consultas.
        self._equatorial_providers = EquatorialProviderChain(
            default_timeout_seconds=Config.POCO_BILL_JOB_TIMEOUT_SECONDS
        )
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
            self.pending_actions[chat_id] = intent_data
            return "⚠️ *Ação sensível detectada.* Digite **confirmar** ou **cancelar**."

        return await self._execute_intent(intent, action, params, chat_id)

    async def _execute_intent(self, intent: str, action: str, params: Dict[str, Any], chat_id: int) -> str:
        # ---------------- COMMAND LIST (NEW) ----------------
        if intent == "command_list":
            return (
                "📜 **MANUAL DE COMANDOS — ROD DO CERRADO**\n"
                "_Lista completa de tudo que eu entendo e executo._\n\n"

                "🌐 **REDE & SEGURANÇA**\n"
                "• `quem ta na rede` → Varredura de dispositivos conectados.\n"
                "• `velocidade da internet` → Teste de velocidade (Speedtest).\n"
                "• `status da internet` → Teste de latência (Ping).\n"
                "• `estatisticas de rede` → Dados do AdGuard (queries, blocks).\n"
                "• `renomear [IP] para [NOME]` → Dar apelido a um dispositivo.\n"
                "• `bloquear [IP]` → Bloquear acesso à internet do dispositivo.\n"
                "• `bloquear [SITE]` → Bloquear domínio (ex: youtube.com).\n\n"

                "⏰ **AGENDA & LEMBRETES**\n"
                "• `lembrar de [TEXTO] [TEMPO]` → Criar lembrete.\n"
                "   _Ex: 'lembrar de tirar o lixo as 18h'_\n"
                "   _Ex: 'lembrar de tomar remedio a cada 8h'_\n"
                "• `listar lembretes` → Ver agenda ativa.\n"
                "• `cancelar lembrete [ID]` → Apagar pelo número.\n"
                "• `editar lembrete [ID] [NOVO TEXTO/HORA]` → Alterar.\n\n"

                "💧 **HIDRATAÇÃO**\n"
                "• `ativar hidratação` → Configuração inicial guiada.\n"
                "• `bebi` ou `tomei agua` → Registrar consumo.\n"
                "• `status hidratação` → Meta vs Consumido.\n"
                "• `analise de hidratação` → Relatório de 30 dias.\n"
                "• `pausar/retomar hidratação` → Controle do fluxo.\n"
                "• `mudar meta para [X]` → Ajustar meta diária.\n\n"

                "🖥️ **SISTEMA**\n"
                "• `status do sistema` → CPU, RAM, Temp, Uptime.\n"
                "• `logs do sistema` → Últimos eventos registrados.\n"
                "• `reiniciar sistema` → Reboot do Raspberry Pi.\n"
                "• `reiniciar adguard` → Restart do container DNS.\n\n"

                "🧾 **CONTAS & FATURAS**\n"
                "• `conta de luz casa` → Consulta a Equatorial no portal oficial pelo Poco.\n"
                "• `conta de agua kitnet 01` → Consulta a Saneago pelo app oficial.\n"
                "• No resultado: botões de Pix copia e cola, boleto em PDF e atualizar.\n"
                "• Nenhum pagamento é iniciado; o ROD só entrega o código e o arquivo.\n\n"

                "🤖 **AUTOMAÇÕES & OUTROS**\n"
                "• `listar automacoes` → Ver regras ativas.\n"
                "• `config automacoes` → Informações sobre config.\n"
                "• `quem é você` → Identidade.\n"
                "• `ajuda` → Menu interativo principal.\n"
            )

        # ---------------- NETWORK SCAN (UX Aprimorada) ----------------
        if intent == "network_scan":
            # 1. Send Initial Status Message
            status_msg = await self.app.bot.send_message(
                chat_id=chat_id,
                text="⏳ Iniciando varredura profunda da rede...",
            )

            # 2. Callback for Updates
            last_text = ""
            async def update_status(text):
                nonlocal last_text
                if text != last_text:
                    try:
                        await self.app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                            text=text,
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
                    await self.app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        text=final_text,
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
            return RemindersFlow.handle_response(chat_id, text_input, ctx)

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

        if intent == "help":
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton

                # MENU PRINCIPAL (3 submenus)
                keyboard = [
                    [
                        InlineKeyboardButton("🌐 Rede & Segurança", callback_data="menu_rede"),
                        InlineKeyboardButton("⏰ Agenda & Vida", callback_data="menu_agenda")
                    ],
                    [
                        InlineKeyboardButton("⚙️ Automações", callback_data="menu_automacoes"),
                        InlineKeyboardButton("🖥️ Sistema & Controle", callback_data="menu_sistema")
                    ],
                    [
                        InlineKeyboardButton("🧾 Contas & Faturas", callback_data="menu_contas")
                    ],
                    [
                        InlineKeyboardButton("ℹ️ Sobre Mim", callback_data="quem é você")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            except ImportError:
                reply_markup = None

            return {
                "text": (
                    "🧠 **ROD DO CERRADO - CENTRAL DE COMANDO**\n\n"
                    "_Guardião da sua casa digital, operacional 24/7._\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "👋 **O que eu posso fazer por você?**\n\n"
                    "Clique em uma categoria abaixo ou digite sua dúvida naturalmente:\n\n"
                    "🌐 **Rede & Segurança** → Scan, bloqueio, stats\n"
                    "⏰ **Agenda & Vida** → Lembretes, hidratação\n"
                    "⚙️ **Automações** → Regras locais e alertas\n"
                    "🖥️ **Sistema** → Monitoramento, controle\n"
                    "🧾 **Contas & Faturas** → Energia e água pelo Poco\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "_Dica: Você pode falar comigo naturalmente._\n"
                    "_Ex: 'me lembra de ligar pro dentista amanhã'_"
                ),
                "reply_markup": reply_markup
            }

        # --- SUBMENUS ---
        if intent == "menu_rede":
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [
                    [
                        InlineKeyboardButton("🔍 Scan Completo", callback_data="quem ta na rede"),
                        InlineKeyboardButton("🚀 Teste Velocidade", callback_data="velocidade da internet")
                    ],
                    [
                        InlineKeyboardButton("📊 Estatísticas", callback_data="estatisticas de rede"),
                        InlineKeyboardButton("🚫 Bloquear IP", callback_data="ajuda bloquear")
                    ],
                    [
                        InlineKeyboardButton("✏️ Renomear Device", callback_data="ajuda renomear"),
                        InlineKeyboardButton("📡 Status Internet", callback_data="status da internet")
                    ],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            except ImportError:
                reply_markup = None

            return {
                "text": (
                    "🌐 **REDE & SEGURANÇA**\n\n"
                    "_Controle total sobre sua rede doméstica._\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "**🔍 Varredura & Monitoramento**\n"
                    "• `Quem tá na rede?` → Lista TODOS os dispositivos conectados\n"
                    "• `Estatísticas de rede` → Consultas e bloqueios do AdGuard\n"
                    "• `Status da internet` → Ping check em tempo real\n"
                    "• `Velocidade da internet` → Speedtest completo\n\n"
                    "**🚫 Bloqueio & Segurança (AdGuard)**\n"
                    "• `Bloquear 192.168.0.X` → Bloqueia dispositivo específico\n"
                    "• `Bloquear youtube.com` → Bloqueia site/domínio\n"
                    "• Bloqueios alteram regras do AdGuard e pedem confirmação\n\n"
                    "**✏️ Organização**\n"
                    "• `Renomear 192.168.0.15 para TV Sala` → Dá nome aos devices\n"
                    "• Nomes cadastrados aparecem nas próximas varreduras\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "_Tudo integrado com AdGuard Home pra máxima proteção._"
                ),
                "reply_markup": reply_markup
            }

        if intent == "menu_agenda":
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [
                    [
                        InlineKeyboardButton("📋 Ver Lembretes", callback_data="listar lembretes"),
                        InlineKeyboardButton("➕ Criar Lembrete", callback_data="criar lembrete")
                    ],
                    [
                        InlineKeyboardButton("💧 Ativar Hidratação", callback_data="ativar hidratacao"),
                        InlineKeyboardButton("📊 Análise 30 Dias", callback_data="analise de hidratacao")
                    ],
                    [
                        InlineKeyboardButton("✅ Bebi Água", callback_data="bebi agua"),
                        InlineKeyboardButton("📈 Status Água", callback_data="status hidratacao")
                    ],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            except ImportError:
                reply_markup = None

            return {
                "text": (
                    "⏰ **AGENDA & BEM-ESTAR**\n\n"
                    "_Gestão de tempo e saúde inteligente._\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "**📅 Lembretes Inteligentes**\n"
                    "• `Lembrar de X amanhã às 14h` → Lembrete único\n"
                    "• `Lembrar de Y a cada 8 horas` → Recorrente\n"
                    "• `Listar lembretes` → Ver agenda completa\n"
                    "• `Cancelar lembrete 3` → Deleta por ID\n"
                    "• Botões de Snooze (+15min, +1h) em cada lembrete\n\n"
                    "**💧 Hidratação Gamificada**\n"
                    "• `Ativar hidratação` → Setup interativo\n"
                    "• `Bebi` ou `Bebi 500ml` → Registra consumo\n"
                    "• `Status água` → Progresso do dia\n"
                    "• `Análise de hidratação` → Padrões de 30 dias\n\n"
                    "**📊 Insights Personalizados**\n"
                    "• Detecção de horários de pico\n"
                    "• Identificação de dias fracos\n"
                    "• Streak contador (dias consecutivos)\n"
                    "• Resumo baseado no histórico salvo\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "_Sistema completo de bem-estar integrado._"
                ),
                "reply_markup": reply_markup
            }

        if intent == "menu_automacoes":
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [
                    [
                        InlineKeyboardButton("📋 Ver Automações", callback_data="listar automacoes"),
                        InlineKeyboardButton("⚙️ Config Automações", callback_data="config automacoes")
                    ],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            except ImportError:
                reply_markup = None

            return {
                "text": (
                    "🤖 Automações & Inteligência\n\n"
                    "Regras locais verificáveis. Sem fingir integração que não existe.\n\n"
                    "Toque em Ver Automações para eu listar o que o motor carregou de verdade.\n\n"
                    "Como funciona:\n"
                    "Sistema local de regras simples. Algumas ações dependem de serviços configurados.\n\n"
                    "Criação/edição pelo Telegram ainda não está pronta."
                ),
                "reply_markup": reply_markup
            }

        if intent == "menu_sistema":
            try:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [
                    [
                        InlineKeyboardButton("📊 Diagnóstico", callback_data="status do sistema"),
                        InlineKeyboardButton("🔄 Reiniciar", callback_data="ajuda reiniciar")
                    ],
                    [
                        InlineKeyboardButton("🛡️ Restart AdGuard", callback_data="reiniciar adguard"),
                        InlineKeyboardButton("📜 Ver Logs", callback_data="logs do sistema")
                    ],
                    [InlineKeyboardButton("📱 Status do Poco", callback_data="status do poco")],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            except ImportError:
                reply_markup = None

            return {
                "text": (
                    "🖥️ **SISTEMA & CONTROLE**\n\n"
                    "_Monitoramento e manutenção do Raspberry Pi._\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "**📊 Monitoramento**\n"
                    "• `Status do sistema` → CPU, RAM, Temperatura\n"
                    "• `Uptime` → Tempo sem reiniciar\n"
                    "• `Uso de disco` → Espaço disponível\n\n"
                    "**🔧 Controle**\n"
                    "• `Reiniciar sistema` → Reboot do Pi (confirmação)\n"
                    "• `Reiniciar AdGuard` → Restart container\n"
                    "• `Logs do sistema` → Últimos eventos\n\n"
                    "**🤖 Sobre o Hardware**\n"
                    "• Raspberry Pi 3B\n"
                    "• Python 3.12\n"
                    "• Docker + Tailscale VPN\n"
                    "• SQLite local\n"
                    "• 100% autonomia\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "_Tudo rodando local, sem cloud._"
                ),
                "reply_markup": reply_markup
            }

        if intent == "menu_contas":
            return self._bills_menu()

        # --- END SUBMENUS ---

        if intent == "system_status": return await SystemModule.get_status()
        if intent == "poco_status":
            return await self._poco_status()
        if intent == "poco_network_check":
            return await self._poco_network_check()
        if intent == "saneago_bills":
            return await self._saneago_bill_flow(chat_id, (params or {}).get("property", "casa"))
        if intent == "equatorial_bills":
            return await self._equatorial_bill_flow(chat_id, (params or {}).get("property", "casa"))
        if intent == "fan_control":
            return await self._handle_fan_control(params.get("text", ""), self.app)
        if intent == "system_reboot": return SystemModule.reboot_device()
        if intent == "system_restart_adguard": return SystemModule.restart_container("adguardhome")

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
            await self.app.bot.send_message(chat_id=chat_id, text="🚀 Iniciando teste de velocidade... segura a onda que demora uns segundos.")
            return await NetworkModule.run_speedtest()

        if intent == "network_status": return await NetworkModule.check_ping()

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
            # Confirmação para ação sensível
            if not params.get("confirmed"):
                self.pending_actions[chat_id] = {
                    "intent": "wake_pc",
                    "params": {"confirmed": True}
                }
                return (
                    "🖥️ *Wake-on-LAN*\n\n"
                    "Vou enviar pacote mágico para ligar o PC.\n\n"
                    "MAC configurado: `{}`\n\n"
                    "Confirma? Digite *confirmar* ou *cancelar*."
                ).format(Config.PC_MAC or "NÃO CONFIGURADO")

            # Executa Wake-on-LAN
            try:
                result = await NetworkModule.wake_on_lan(Config.PC_MAC)
                if not result.get("success"):
                    return f"❌ Erro ao enviar pacote WOL: {result.get('message', 'falha desconhecida')}"

                return (
                    "🖥️ *Pacote WOL Enviado!*\n\n"
                    "Pacote mágico enviado para: `{}`\n\n"
                    "O PC deve ligar em alguns segundos.\n"
                    "Aguarde 30-60 segundos e verifique se está online."
                ).format(Config.PC_MAC)

            except Exception as e:
                logger.error(f"Erro ao executar Wake-on-LAN: {e}")
                return f"❌ Erro ao enviar pacote WOL: {str(e)}"

        # Status do PC
        if intent == "pc_status":
            # Tenta pingar o PC (assumindo que IP está configurado)
            pc_ip = os.getenv("PC_IP", "192.168.0.100")  # IP do PC
            online = await NetworkModule.check_device_online(pc_ip)

            if online:
                return f"🟢 PC está ONLINE ({pc_ip})"
            else:
                return f"🔴 PC está OFFLINE ou não respondendo ({pc_ip})"

        if intent == "context_query":
            try: return f"📊 Resultado técnico:\n```{ContextReader.handle(params)}```"
            except: return "❌ Erro ao analisar histórico."

        if intent == "reminder_set":
            if action == "create_request": return RemindersFlow.start_flow(chat_id, params)
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

        if intent == "energy_status": return "⚡ Monitoramento de energia em fase de coleta."

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
    async def _handle_fan_control(text: str, app) -> str:
        fan_service = app.bot_data.get("fan_service")
        if not fan_service:
            return "❌ Serviço de controle da ventoinha (FanControlService) não está inicializado."

        t = text.lower()
        if "ligar" in t:
            if fan_service.fan:
                fan_service.fan.on()
                fan_service.manual_override = True
                return "🌬️ Ventoinha **ligada** manualmente. O controle automático está pausado. Use 'voltar pro auto' para reativar."
            return "❌ Fan hardware não disponível."
        elif "desligar" in t:
            if fan_service.fan:
                fan_service.fan.off()
                fan_service.manual_override = True
                return "🛑 Ventoinha **desligada** manualmente. O controle automático está pausado. Use 'voltar pro auto' para reativar."
            return "❌ Fan hardware não disponível."
        elif "auto" in t:
            fan_service.manual_override = False
            return "✅ Controle automático da ventoinha reativado."
        else:
            state = "LIGADA" if fan_service.fan and fan_service.fan.is_active else "DESLIGADA"
            override = " (Manual Override)" if fan_service.manual_override else " (Automático)"
            return (
                f"🌬️ *Status da Ventoinha*\n\n"
                f"Estado Atual: **{state}{override}**\n"
                f"GPIO Pin: `{fan_service.pin}`\n"
                f"Liga acima de: `{fan_service.threshold_on}°C`\n"
                f"Desliga abaixo de: `{fan_service.threshold_off}°C`"
            )

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
        pending = self.pending_actions.pop(chat_id, None)
        if not pending: return "⚠️ Nenhuma ação pendente para confirmar."
        logger.info(f"Ação confirmada pelo usuário: {pending}")
        return await self._execute_intent(pending.get("intent"), pending.get("action", "default"), pending.get("params", {}), chat_id)

    @staticmethod
    async def _run_poco_job(action: str, timeout_seconds: int = 70, params: dict | None = None):
        if not Config.POCO_NODE_ENABLED:
            return None, "O nó Poco está desativado na configuração."
        from jarvis.api.app import get_poco_service

        service = get_poco_service()
        if not service.status().get("online"):
            return None, "O Poco está offline ou sem heartbeat recente."
        job = service.enqueue(action, params=params or {}, ttl_seconds=timeout_seconds + 30)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current = service.get_job(job.job_id)
            if current and current.status == "completed":
                return current.result or {}, None
            if current and current.status in {"failed", "expired"}:
                return None, current.error or "A tarefa expirou antes de concluir."
            await asyncio.sleep(2)
        return None, "O Poco não concluiu a tarefa dentro do tempo esperado."

    @staticmethod
    def _equatorial_code(error_text: str) -> str:
        """Código tipado emitido pelo agente Android, venha ele embrulhado ou não.

        O agente monta a mensagem como ``classe: mensagem`` antes de devolvê-la,
        então o que chega no fio é ``IllegalStateException: EQUATORIAL_...``.
        Casar pelo início da string parecia certo e nunca funcionou: dos 37 erros
        registrados em produção, nenhum começava com ``EQUATORIAL_`` e todos
        começavam com o nome da exceção. Procurar o código em qualquer posição
        sobrevive a qualquer embrulho que o Android venha a usar.

        A extração vive em ``services.equatorial_providers`` porque a cadeia de
        canais decide cooldown pelo mesmo código. Duas expressões regulares para o
        mesmo contrato divergiriam no primeiro código novo.
        """
        return equatorial_code(error_text)

    async def _poco_bill_cache_note(self, provider: str, property_key: str) -> str:
        """Última leitura confirmada guardada no Poco.

        Vale como consolo quando a consulta ao vivo falha, mas só pode aparecer
        com data explícita. Cache apresentado como medição atual seria mentira.
        """
        result, error = await self._run_poco_job(
            "read_bill_cache", 30, {"provider": provider, "property": property_key}
        )
        if error or not result:
            return ""
        return self._cache_note_text(result)

    @staticmethod
    def _cache_note_text(result: dict | None) -> str:
        """Rótulo da leitura guardada: valor, vencimento e IDADE explícita.

        Só estes campos, e só se vieram. Código de barras e Pix existem em parte
        das leituras guardadas e não podem aparecer aqui em nenhuma hipótese:
        seriam um código de pagamento antigo colado ao lado de um valor antigo, o
        que qualquer pessoa leria como a cobrança de agora.

        O rótulo "Última leitura confirmada ... cache do Poco" é contrato de
        outro agente e fica. O que sai é o defeito: ``vencimento indisponível``,
        rótulo inventado para um dado que a leitura não trouxe. Entram o marcador
        🟠, que separa esta parte do cartão ao vivo, e a linha que diz o que fazer
        a respeito.
        """
        return bill_screen.render_stale_block(result)

    async def _poco_status(self) -> str:
        from jarvis.api.app import get_poco_service

        status = get_poco_service().status()
        heartbeat = status.get("heartbeat") or {}
        if not status.get("online"):
            return "Poco: offline ou sem sinal recente. O ROD no Pi continua funcionando."
        battery = heartbeat.get("battery_level")
        temperature = heartbeat.get("battery_temperature_c")
        wifi = "conectado" if heartbeat.get("wifi_connected") else "desconectado"
        return f"Poco: online. Bateria: {battery:.0f}%, Temp: {temperature:.1f} °C, Wi-Fi: {wifi}."

    async def _poco_network_check(self) -> str:
        result, error = await self._run_poco_job("network_check", 45)
        if error:
            return f"Não consegui validar pelo Poco: {error}"
        if result.get("internet_validated"):
            return "Validação pelo Poco: Wi-Fi conectado e internet confirmada pelo Android."
        if result.get("wifi_connected"):
            return "Validação pelo Poco: Wi-Fi conectado, mas sem acesso à internet confirmado."
        return "Validação pelo Poco: Wi-Fi desconectado."

    async def _poco_saneago_bills(self, params: dict | None = None) -> str:
        """Texto da consulta de água, sem tocar no Telegram.

        Quem manda mensagem é ``_saneago_bill_flow``: a UX pede UMA mensagem
        editada no fim, e o aviso que ficava aqui deixava duas.
        """
        property_key = (params or {}).get("property", "casa")
        text, _ok = await self._saneago_bill_card(property_key)
        return text

    async def _saneago_bill_card(self, property_key: str):
        """(texto, deu_certo). Nunca levanta: o single-flight é compartilhado."""
        try:
            result, error = await self._run_poco_job(
                "refresh_saneago_bills",
                Config.POCO_BILL_JOB_TIMEOUT_SECONDS,
                {"property": property_key},
            )
        except Exception:
            logger.exception("Falha inesperada na consulta da Saneago")
            return SANEAGO_GENERIC_FAILURE_MESSAGE, False
        if error:
            text = self._saneago_failure_message(str(error), property_key)
            note = await self._poco_bill_cache_note("saneago", property_key)
            if note:
                self._bill_stale_only[("saneago", property_key)] = True
            return text + note, False
        # Só uma leitura concluída prova que esta unidade existe no cofre do Poco.
        # Sem este registro, o menu do imóvel prometia "peça a conta de água uma
        # vez para eu confirmar" e nunca confirmava: o botão 💧 Água não podia
        # nascer em nenhuma hipótese, porque nada gravava a água como confirmada.
        self._remember_bill_property("saneago", property_key)
        self._bill_stale_only[("saneago", property_key)] = False
        return (
            bill_screen.render_bill_card(
                "saneago",
                self._property_label(property_key),
                result,
                read_at=self._clock(),
            ),
            True,
        )

    def _saneago_failure_message(self, error_text: str, property_key: str) -> str:
        """Falha de água traduzida para uma frase com próximo passo.

        A saída antiga terminava em ``: {error}`` — o texto cru do telefone na
        tela do dono, com nome de exceção e detalhe de automação. Isso fica no
        log, que é onde serve para algo.
        """
        text = str(error_text or "")
        lowered = text.lower()
        logger.info("Falha na Saneago classificada para a tela do dono")
        if "acessibilidade nao respondeu" in lowered or "acessibilidade não respondeu" in lowered:
            return (
                "A automação do ROD está desativada no Poco. Abra Configurações > "
                "Acessibilidade > Aplicativos baixados e ative ROD — automação local."
            )
        if "sessao saneago expirada" in lowered or "sessão saneago expirada" in lowered:
            return (
                "A sessão da Saneago expirou. O ROD tentará entrar novamente usando o "
                "cofre local do Poco."
            )
        if "numero da conta" in lowered or "número da conta" in lowered:
            return (
                "Li a tela da Saneago mas não consegui confirmar qual conta é esta. "
                "Não vou atribuir essa fatura a nenhum imóvel sem essa confirmação."
            )
        if "nao apareceu no seletor" in lowered or "não apareceu no seletor" in lowered:
            return (
                f"A unidade {self._property_label(property_key)} não aparece entre as contas "
                "vinculadas a este login da Saneago. Não usei dados de outro imóvel."
            )
        if any(
            marker in lowered
            for marker in (
                "poco está offline",
                "poco esta offline",
                "sem heartbeat",
                "nó poco está desativado",
                "no poco esta desativado",
                "não confirmou o início",
                "nao confirmou o inicio",
            )
        ):
            return POCO_UNAVAILABLE_MESSAGE
        return SANEAGO_GENERIC_FAILURE_MESSAGE

    async def _poco_equatorial_bills(self, params: dict | None = None) -> str:
        """Texto da consulta, sem tocar no Telegram.

        Quem manda mensagem é o fluxo (``_equatorial_bill_flow``): a UX pede UMA
        mensagem editada no fim, e um aviso intermediário aqui deixava duas.
        """
        property_key = (params or {}).get("property", "casa")
        outcome = await self._equatorial_chain_read(property_key)
        return self._equatorial_outcome_text(property_key, outcome)

    async def _equatorial_chain_read(self, property_key: str, *, forced: bool = False):
        """Percorre os canais oficiais até um entregar a fatura.

        A cadeia é política pura: quem executa continua sendo ``_run_poco_job``,
        passado como ``runner``. Isso mantém um único ponto de contato com a fila
        do Poco e deixa a decisão testável sem telefone.
        """
        outcome = await self._equatorial_providers.read(
            property_key, self._run_poco_job, ignore_cooldown=forced
        )
        # Trilha com nome de canal e código tipado: material de log. A tela do dono
        # nunca recebe nome de canal — ele pediu a conta, não o mapa da automação.
        logger.info("Cadeia Equatorial concluída | %s", outcome.trail)
        return outcome

    def _equatorial_outcome_text(self, property_key: str, outcome) -> str:
        """Texto final da consulta, sem nomear canal interno.

        Três desfechos e nenhum meio-termo: leitura ao vivo, leitura guardada
        (rotulada, com o motivo acionável de a consulta de agora não ter vindo) ou
        só a falha humanizada.

        Existe UMA forma do texto da leitura, e ela é o cartão. Havia uma segunda,
        longa, que sobreviveu por um tempo porque os testes a liam como saída
        observável do pipeline — e ela colava o Pix e o código de barras no TEXTO
        da mensagem. Texto vive no histórico do chat para sempre, e aquele caminho
        contornava de uma vez todas as travas de frescor: TTL, referência igual e
        trava de leitura antiga protegem os BOTÕES. Um mês depois o dono rolaria a
        conversa, encontraria o Pix ao lado de um valor e pagaria a fatura do mês
        passado sem ter como perceber. Um formatador que ninguém chama mas que
        sabe imprimir código de pagamento é uma arma carregada guardada em casa.
        """
        if outcome.ok:
            return bill_screen.render_bill_card(
                "equatorial",
                self._property_label(property_key),
                outcome.result,
                read_at=self._clock(),
            )
        failure = self._equatorial_failure_message(
            str(outcome.failure_text or "").strip(), property_key
        )
        if outcome.informational:
            return failure + self._cache_note_text(outcome.result)
        return failure

    def _equatorial_failure_message(self, error_text: str, property_key: str) -> str:
        """Traduz a falha para uma frase que o dono pode agir a respeito.

        Nada de ``IllegalStateException``, traceback ou código ``EQUATORIAL_*`` na
        tela: eles não dizem ao dono o que fazer e vazam detalhe de automação. O
        código continua no log, que é onde ele serve para algo.
        """
        text = str(error_text or "")
        lowered = text.lower()
        # Erros tipados do agente Android vêm antes da heurística por palavra-chave.
        # Sessão expirada não é falha de infraestrutura: pedir login humano uma vez
        # é mais honesto (e mais barato) do que repetir tentativas cegas no Poco.
        code = self._equatorial_code(text)
        logger.info("Falha na Equatorial classificada como %s", code or "sem código tipado")

        # Verificação humana em TODOS os canais é diferente de um desafio numa tela:
        # não existe próximo passo automático, e o dono precisa ouvir que nada foi pago.
        if code == "EQUATORIAL_HUMAN_CHECK_ALL_CHANNELS" or "todos os canais" in lowered:
            return HUMAN_CHECK_ALL_CHANNELS_MESSAGE

        if code == "EQUATORIAL_AUTH_REQUIRED":
            # É a falha que o dono mais encontra: o ROD entra no portal de acesso,
            # mas a sessão que ele consegue não alcança o host das faturas, então
            # não há renovação automática possível hoje. Dizer isso de frente evita
            # que ele fique repetindo ATUALIZAR à espera de uma recuperação sozinha,
            # e o próximo passo aponta o botão exato em vez de "repita a consulta".
            return (
                "A sessão da Equatorial expirou no Poco e eu não consigo renovar essa "
                "sessão sozinho. Abra o Chrome do Poco, faça login novamente na Equatorial "
                "e toque em ATUALIZAR aqui — a resposta vem nesta mesma mensagem."
            )
        if code == "EQUATORIAL_HUMAN_CHECK" or any(
            marker in lowered for marker in ("captcha", "imperva", "verificacao humana")
        ):
            return (
                "A Equatorial pediu verificação humana no Poco. Resolva a tela uma vez e "
                "repita a consulta; o ROD não tenta contornar o bloqueio."
            )
        # Cada código diz o que fazer. Devolver só "falhou" obrigaria abrir o
        # logcat do Poco para descobrir se o problema é do portal, do cadastro
        # ou da leitura.
        typed = {
            # O portal recusa login automático em silêncio: recarrega a tela de
            # acesso sem dizer nada. O motivo é o motor antifraude dele, que
            # pontua a sessão em vez de apresentar desafio. Não há o que o dono
            # conserte no cadastro, então a mensagem não manda procurar defeito.
            "EQUATORIAL_LOGIN_FAILED": (
                "A Equatorial não aceitou a entrada automática — o portal dela avalia o acesso "
                "por um sistema antifraude e recusou sem informar motivo. Abrir o Chrome do Poco "
                "e entrar uma vez restabelece a consulta. Nenhum pagamento foi feito."
            ),
            "EQUATORIAL_LOGIN_REJECTED": (
                "A Equatorial recusou os dados de acesso guardados no cofre do Poco. "
                "Vale conferir a unidade consumidora e o documento cadastrados."
            ),
            "EQUATORIAL_CREDENTIALS_MISSING": (
                "Faltam dados de acesso da Equatorial no cofre do Poco. "
                "Cadastre unidade consumidora e documento no aplicativo ROD."
            ),
            "EQUATORIAL_WEBVIEW_UNAVAILABLE": (
                "O navegador interno do ROD não subiu no Poco desta vez. Vale repetir a consulta."
            ),
            "EQUATORIAL_PIX_NOT_FOUND": (
                "Não encontrei o Pix desta fatura na tela do portal. Nenhum pagamento foi feito."
            ),
            "EQUATORIAL_PIX_AMBIGUOUS": (
                "O portal mostrou mais de um código Pix e não consigo saber qual é desta fatura. "
                "Prefiro não enviar nada a enviar o Pix de outra conta."
            ),
            "EQUATORIAL_PIX_INVALID": (
                "O código Pix que li não passou na validação oficial do BR Code. "
                "Não vou entregar um código de pagamento que pode estar corrompido."
            ),
            "EQUATORIAL_BOLETO_NOT_FOUND": (
                "O portal não ofereceu o boleto desta fatura agora. Tente novamente em alguns minutos."
            ),
            "EQUATORIAL_BOLETO_TOO_LARGE": (
                "O arquivo do boleto veio maior do que o limite seguro e foi descartado."
            ),
            "EQUATORIAL_BOLETO_NOT_SENT": (
                "Consegui o boleto mas falhei ao entregá-lo. Tente novamente."
            ),
            "EQUATORIAL_PROPERTY_NOT_MAPPED": (
                f"Ainda não sei qual conta contrato do portal corresponde a "
                f"{self._property_label(property_key)}. O ROD aprende isso sozinho na "
                "primeira consulta bem-sucedida; se persistir, confira a unidade consumidora "
                "cadastrada no cofre do Poco."
            ),
            # Sinônimo emitido hoje pelo agente Android.
            "EQUATORIAL_UC_NAO_ENCONTRADA": (
                "O imóvel pedido não apareceu na lista de contratos desse login da Equatorial. "
                "Não usei dados de outro imóvel."
            ),
            "EQUATORIAL_CONTRACT_NOT_FOUND": (
                "O imóvel pedido não apareceu na lista de contratos desse login da Equatorial. "
                "Não usei dados de outro imóvel."
            ),
            "EQUATORIAL_BILL_NOT_FOUND": (
                "Cheguei ao imóvel certo no portal, mas nenhuma fatura estava visível na tela. "
                "Pode não haver fatura em aberto agora."
            ),
            "EQUATORIAL_PAYMENT_DATA_NOT_FOUND": (
                "Li a fatura, mas o portal não expôs código de barras nem PIX nesta tela. "
                "Não vou inventar um código de pagamento."
            ),
            "EQUATORIAL_PORTAL_TIMEOUT": (
                "O portal da Equatorial não respondeu a tempo no Poco. "
                "Vale repetir a consulta em alguns minutos."
            ),
        }
        if code in typed:
            return typed[code]

        # Falha de infraestrutura do nó: não é problema da concessionária e não há
        # o que o dono resolva no portal.
        if any(
            marker in lowered
            for marker in (
                "poco está offline",
                "poco esta offline",
                "sem heartbeat",
                "nó poco está desativado",
                "no poco esta desativado",
                "não confirmou o início",
                "nao confirmou o inicio",
            )
        ):
            return POCO_UNAVAILABLE_MESSAGE

        # Portal fora do ar sem código tipado (5xx, DNS, conexão recusada).
        if any(
            marker in lowered
            for marker in ("502", "503", "504", "err_", "net::", "unreachable", "connection")
        ):
            return PORTAL_UNAVAILABLE_MESSAGE

        return BILL_GENERIC_FAILURE_MESSAGE

    @staticmethod
    def _clock() -> str:
        """Hora local para datar a leitura. Sem hora, "agora" mente no histórico."""
        try:
            return datetime.now(Config.TZ).strftime("%H:%M")
        except Exception:
            logger.debug("Não consegui ler a hora local", exc_info=True)
            return ""

    # =====================================================
    # CONTAS & FATURAS — UX NO TELEGRAM
    # =====================================================
    @staticmethod
    def _property_label(property_key: str) -> str:
        return str(property_key or "casa").replace("_", " ").strip().title()

    def _flight_in_progress(self, provider: str, property_key: str, action: str) -> bool:
        task = self._bill_flights.get((provider, property_key, action))
        return task is not None and not task.done()

    async def _single_flight(
        self, provider: str, property_key: str, action: str, factory, *, forced: bool = False
    ):
        """Uma automação por (concessionária, imóvel, ação).

        O Poco executa um job por vez. Dois toques rápidos no mesmo botão criavam
        dois jobs iguais: o segundo esperava o primeiro terminar e devolvia o mesmo
        dado depois do dobro do tempo. Aqui o segundo interessado espera a operação
        que já existe. O ``shield`` evita que um chamador que desistiu (timeout do
        Telegram, mensagem apagada) cancele o job de quem ainda espera.

        ``forced`` é o ATUALIZAR apertado pelo dono, e ele NÃO adere a um voo que
        não foi forçado. O caso é concreto: a consulta demora minutos, o dono se
        impacienta e aperta ATUALIZAR — e antes ele herdava o resultado da consulta
        antiga, que respeitou o cooldown. O canal preferido nunca chegava a ser
        tentado, e ele recebia a recusa lembrada com a impressão, correta, de que
        o botão não fez nada. Espera-se o voo em curso terminar (o telefone é um
        só) e AÍ se faz a tentativa nova, em vez de disputar o aparelho com ele.

        Devolve ``(resultado, reaproveitado)``.
        """
        key = (provider, property_key, action)
        while True:
            existing = self._bill_flights.get(key)
            if existing is None or existing.done():
                break
            outcome = await asyncio.shield(existing)
            if not forced or self._bill_flight_forced.get(key, False):
                return outcome, True
            # Reobserva: outro toque forçado pode ter começado enquanto esperávamos,
            # e nesse caso aderir a ELE é o certo — dois jobs forçados seguidos só
            # fariam o dono esperar duas vezes pela mesma resposta.
        task = asyncio.ensure_future(factory())
        self._bill_flights[key] = task
        self._bill_flight_forced[key] = forced
        try:
            return await asyncio.shield(task), False
        finally:
            if self._bill_flights.get(key) is task and task.done():
                self._bill_flights.pop(key, None)
                self._bill_flight_forced.pop(key, None)

    @staticmethod
    def _telegram_keyboard_classes():
        """``(Markup, Button)`` ou ``(None, None)``. Falta de lib não derruba a UX."""
        try:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton

            return InlineKeyboardMarkup, InlineKeyboardButton
        except ImportError:
            return None, None

    def _bill_keyboard(
        self,
        provider: str,
        property_key: str,
        *,
        payment: bool = True,
        pix: bool | None = None,
        boleto: bool | None = None,
        shortcuts: bool = True,
    ):
        """Teclado do cartão. Só oferece o que é possível NESTE estado.

        ``pix``/``boleto`` existem para o caso em que uma das duas entregas acabou
        de falhar: repetir o botão que falhou é convidar o dono a esperar o mesmo
        erro, e esconder os dois o deixa sem próximo passo. Então a recusa do Pix
        mantém BOLETO, e a do boleto mantém PIX.
        """
        InlineKeyboardMarkup, InlineKeyboardButton = self._telegram_keyboard_classes()
        if InlineKeyboardButton is None:
            return None
        show_pix = payment if pix is None else pix
        show_boleto = payment if boleto is None else boleto
        rows = []
        payment_row = []
        if show_pix:
            payment_row.append(
                InlineKeyboardButton("💠 PIX", callback_data=f"bill_pix:{provider}:{property_key}")
            )
        if show_boleto:
            payment_row.append(
                InlineKeyboardButton("📄 BOLETO", callback_data=f"bill_boleto:{provider}:{property_key}")
            )
        if payment_row:
            rows.append(payment_row)
        rows.append(
            [
                InlineKeyboardButton("🔄 ATUALIZAR", callback_data=f"bill_refresh:{provider}:{property_key}"),
                InlineKeyboardButton("🔙 VOLTAR", callback_data="menu_contas"),
            ]
        )
        if shortcuts:
            rows.extend(self._bill_shortcut_rows(provider, property_key, InlineKeyboardButton))
        return InlineKeyboardMarkup(rows)

    def _bill_shortcut_rows(self, provider: str, property_key: str, button_class):
        """Atalhos que economizam voltas no menu.

        Do cartão de energia da casa até a conta de água da casa eram quatro
        toques (VOLTAR → imóvel → menu do imóvel → Água) para uma informação da
        MESMA tela. Trocar de imóvel custava o mesmo. Aqui vira um toque, e só
        aparece para o que uma consulta concluída já provou existir — atalho para
        imóvel que eu não sei ler seria botão que só sabe falhar.
        """
        rows = []
        confirmed = self._confirmed_bill_properties()
        other = "saneago" if provider == "equatorial" else "equatorial"
        if property_key in confirmed.get(other, []):
            rows.append(
                [
                    button_class(
                        bill_screen.shortcut_label(other),
                        callback_data=f"bill_refresh:{other}:{property_key}",
                    )
                ]
            )
        siblings = [key for key in confirmed.get(provider, []) if key != property_key]
        row = []
        for key in bill_screen.limited(siblings):
            row.append(
                button_class(
                    bill_screen.shortcut_label(provider, key),
                    callback_data=f"bill_refresh:{provider}:{key}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    def _bill_back_keyboard(self):
        """Só VOLTAR. Para a mensagem que só precisa não ser um beco sem saída."""
        InlineKeyboardMarkup, InlineKeyboardButton = self._telegram_keyboard_classes()
        if InlineKeyboardButton is None:
            return None
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 VOLTAR", callback_data="menu_contas")]]
        )

    async def _send_bill_text(self, chat_id: int, text: str, reply_markup=None, parse_mode=None):
        kwargs = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        try:
            message = await self.app.bot.send_message(**kwargs)
            return getattr(message, "message_id", None)
        except Exception:
            logger.warning("Não consegui enviar a mensagem de fatura no Telegram", exc_info=True)
            return None

    async def _replace_bill_message(self, chat_id: int, message_id, text: str, reply_markup=None):
        """Edita a mensagem da consulta em vez de empilhar avisos no chat.

        Duas mensagens ("estou consultando" e "resultado") viraram poluição real:
        uma consulta leva minutos e o dono ficava com o histórico cheio de avisos
        obsoletos. Falha de edição (mensagem apagada, texto idêntico) não pode
        derrubar o fluxo — cai para uma mensagem nova.
        """
        if message_id is not None:
            try:
                await self.app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                return message_id
            except Exception:
                logger.debug("Edição da mensagem de fatura falhou", exc_info=True)
                return await self._send_bill_text(chat_id, text, reply_markup)
        return await self._send_bill_text(chat_id, text, reply_markup)

    async def _bill_progress(self, chat_id: int, message_id, provider: str, label: str):
        """Mantém presença verdadeira durante uma consulta demorada.

        Não há porcentagem: os portais não informam progresso mensurável. A cada
        etapa apenas dizemos o que o ROD de fato sabe — o job foi entregue ao
        Poco e ainda está em execução — e renovamos o indicador ``typing`` do
        Telegram, que expira sozinho em poucos segundos.
        """
        stages = (
            "O Poco recebeu a consulta e está abrindo o serviço.",
            "A consulta continua no Poco; estou aguardando a tela da fatura.",
            "Ainda estou conferindo a resposta. Não precisa tocar novamente.",
        )
        try:
            # Consulta rápida não pisca nem gera edições inúteis.
            await asyncio.sleep(4)
            for stage in stages:
                action = getattr(self.app.bot, "send_chat_action", None)
                if callable(action):
                    try:
                        await action(chat_id=chat_id, action="typing")
                    except Exception:
                        logger.debug("Indicador typing indisponível", exc_info=True)
                await self._replace_bill_message(
                    chat_id,
                    message_id,
                    f"{bill_screen.render_wait(provider, label)}\n\n{stage}\n\n◌ Em andamento",
                )
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Progresso é apresentação: nunca pode derrubar a consulta real.
            logger.debug("Não consegui atualizar o progresso da fatura", exc_info=True)

    async def _equatorial_bill_flow(
        self, chat_id: int, property_key: str, query=None, *, forced: bool = False
    ):
        """Consulta de energia. ``forced`` = o dono APERTOU ATUALIZAR.

        Aí a cadeia tenta o canal preferido mesmo em cooldown, porque o dedo dele
        é a informação nova que o cooldown supunha não existir.
        """
        return await self._bill_flow(
            chat_id,
            "equatorial",
            property_key,
            query=query,
            card=lambda: self._equatorial_bill_card(property_key, forced=forced),
            forced=forced,
        )

    async def _saneago_bill_flow(self, chat_id: int, property_key: str, query=None):
        """Consulta de água pela MESMA tela da energia.

        Antes a água era o patinho feio: duas mensagens (um aviso solto e um
        resultado), nenhum botão no fim — o dono tinha que digitar de novo para
        repetir — e o texto do erro do telefone copiado cru na tela.
        """
        return await self._bill_flow(
            chat_id,
            "saneago",
            property_key,
            query=query,
            card=lambda: self._saneago_bill_card(property_key),
        )

    async def _bill_flow(
        self, chat_id: int, provider: str, property_key: str, *, query, card, forced: bool = False
    ):
        """Consulta com UMA mensagem: abre com o aviso e termina editando-a.

        O aviso agora diz quanto tempo isso costuma levar e que a resposta chega
        nesta mesma mensagem. Sem isso, o silêncio de minutos parecia travamento e
        o dono tocava de novo — e o segundo toque não acelera nada, porque o
        telefone executa um job por vez.
        """
        label = self._property_label(property_key)
        header = bill_screen.render_wait(provider, label)
        if query is not None:
            message_id = getattr(getattr(query, "message", None), "message_id", None)
            await self._replace_bill_message(chat_id, message_id, header)
        else:
            message_id = await self._send_bill_text(chat_id, header)
        progress = asyncio.create_task(self._bill_progress(chat_id, message_id, provider, label))
        try:
            (text, ok), _reused = await self._single_flight(
                provider, property_key, "bills", card, forced=forced
            )
        finally:
            progress.cancel()
            try:
                await progress
            except asyncio.CancelledError:
                pass
        # Água não tem entrega de artefato hoje: oferecer PIX ali seria um botão
        # que só sabe dizer "não habilitado".
        payment = ok and provider == "equatorial"
        keyboard = self._bill_keyboard(provider, property_key, payment=payment)
        await self._replace_bill_message(chat_id, message_id, text, keyboard)
        return None

    async def _equatorial_bill_card(self, property_key: str, *, forced: bool = False):
        """(texto, pode_pagar). Nunca levanta: o single-flight é compartilhado.

        ``pode_pagar`` decide os botões PIX e BOLETO, e por isso é ``False`` também
        quando a leitura veio do cache: oferecer pagamento sobre leitura antiga é o
        contrário do que esses botões prometem.
        """
        try:
            outcome = await self._equatorial_chain_read(property_key, forced=forced)
        except Exception:
            logger.exception("Falha inesperada na consulta da Equatorial")
            return BILL_GENERIC_FAILURE_MESSAGE, False
        text = self._equatorial_outcome_text(property_key, outcome)
        key = ("equatorial", property_key)
        if outcome.ok:
            self._remember_bill_property("equatorial", property_key)
            self._bill_reference[key] = str((outcome.result or {}).get("reference") or "").strip()
            self._bill_stale_only[key] = False
            return text, True
        if outcome.informational:
            self._forget_bill_payment_state("equatorial", property_key)
        return text, False

    def _forget_bill_payment_state(self, provider: str, property_key: str) -> None:
        """Cache virou a informação mais recente: nada de pagamento sobrevive.

        A referência confirmada e os artefatos em memória descrevem uma fatura que
        eu não consigo mais confirmar. Mantê-los deixaria um toque em PIX de uma
        mensagem antiga entregar código de pagamento como se fosse o de agora.
        """
        key = (provider, property_key)
        self._bill_reference.pop(key, None)
        for kind in ("pix", "boleto"):
            self._bill_artifacts.pop((provider, property_key, kind), None)
        self._bill_stale_only[key] = True

    # ---------- ARTEFATOS DE PAGAMENTO (PIX / BOLETO) ----------
    async def _run_poco_bill_action(self, action: str, property_key: str):
        """Único ponto de contato com as ações de artefato do nó Android.

        Enquanto a fila do Poco não aceitar a ação, ``enqueue`` levanta
        ``ValueError``; o dono precisa de uma frase honesta, não de um traceback.

        As duas ações de artefato de hoje saem da sessão web. Se ela acabou de ter
        o acesso RECUSADO, enfileirar de novo é vender ao dono minutos de espera
        para entregar a mesma recusa: o cooldown responde na hora, com a mesma
        orientação. Só a recusa dispensa a tentativa — um tropeço de portal lento
        não, porque ele pode não repetir e transformá-lo em "não posso" seria
        mentira. O resultado da tentativa realimenta a saúde do canal, então uma
        recusa no Pix também poupa a próxima consulta.
        """
        remembered = self._equatorial_providers.refusal_reason(WEB_SESSION)
        if remembered:
            logger.info("Ação de artefato dispensada: canal em cooldown (%s)", remembered)
            return None, self._equatorial_failure_message(remembered, property_key)
        try:
            result, error = await self._run_poco_job(
                action,
                Config.POCO_BILL_JOB_TIMEOUT_SECONDS,
                {"provider": "equatorial", "property": property_key},
            )
        except ValueError:
            logger.info("Ação de artefato ainda não habilitada na fila do Poco: %s", action)
            return None, BILL_ACTION_UNAVAILABLE_MESSAGE
        except Exception:
            logger.exception("Falha ao enfileirar ação de artefato no Poco")
            return None, POCO_UNAVAILABLE_MESSAGE
        if error:
            self._equatorial_providers.record_failure(WEB_SESSION, error)
            return None, self._equatorial_failure_message(str(error).strip(), property_key)
        self._equatorial_providers.record_success(WEB_SESSION)
        return result or {}, None

    @staticmethod
    def _artifact_store():
        """Canal de artefato do Pi, se já existir nesta versão.

        Concentrar a dependência num método só significa que trocar o contrato
        (hoje ``get_artifact_store().resolve``/``consume``) é uma edição local, e
        que a ausência do canal vira frase honesta em vez de traceback.
        """
        try:
            from jarvis.api import app as api_app

            factory = getattr(api_app, "get_artifact_store", None)
            return factory() if callable(factory) else None
        except Exception:
            logger.warning("Canal de artefato indisponível", exc_info=True)
            return None

    async def _resolve_poco_artifact(self, artifact_id):
        """Traduz o id opaco em caminho de arquivo temporário local."""
        if not artifact_id:
            return None, BILL_ARTIFACT_UNAVAILABLE_MESSAGE
        resolvers = []
        store = self._artifact_store()
        if store is not None:
            resolvers.append(getattr(store, "resolve", None))
        try:
            from jarvis.api import app as api_app

            resolvers.append(getattr(api_app, "resolve_poco_artifact", None))
        except Exception:
            logger.debug("API local indisponível para resolver artefato", exc_info=True)
        for resolver in resolvers:
            if not callable(resolver):
                continue
            try:
                path = resolver(artifact_id)
                if inspect.isawaitable(path):
                    path = await path
            except Exception:
                logger.warning("Resolução do artefato falhou", exc_info=True)
                return None, BILL_ARTIFACT_UNAVAILABLE_MESSAGE
            if path and os.path.exists(str(path)):
                return str(path), None
            return None, BILL_ARTIFACT_UNAVAILABLE_MESSAGE
        logger.info("Nenhum resolvedor de artefato disponível no Pi ainda")
        return None, BILL_ACTION_UNAVAILABLE_MESSAGE

    def _release_artifact(self, artifact_id, path):
        """Entrega feita: o artefato deixa de existir no Pi.

        Preferir ``consume`` do canal a apagar o arquivo na mão mantém metadados e
        arquivo consistentes; o unlink direto é só a rede de segurança.
        """
        store = self._artifact_store()
        consumer = getattr(store, "consume", None) if store is not None else None
        if artifact_id and callable(consumer):
            try:
                consumer(artifact_id)
                return
            except Exception:
                logger.debug("Não consegui consumir o artefato pelo canal", exc_info=True)
        self._discard_temp_artifact(path)

    @staticmethod
    def _discard_temp_artifact(path):
        """Arquivo de pagamento não fica no disco depois de entregue."""
        if not path:
            return
        try:
            os.unlink(str(path))
        except OSError:
            logger.debug("Não consegui apagar o artefato temporário", exc_info=True)

    @staticmethod
    def _artifact_id(result: dict) -> str:
        for field in ("artifact_id", "artifact", "boleto_artifact_id", "pix_artifact_id"):
            value = str((result or {}).get(field) or "").strip()
            if value:
                return value
        return ""

    def _fresh_artifact(self, provider: str, property_key: str, kind: str):
        """Artefato em memória só serve se for da MESMA fatura e ainda recente."""
        cached = self._bill_artifacts.get((provider, property_key, kind))
        if not cached:
            return None
        if time.time() - cached.get("captured_at", 0) > BILL_ARTIFACT_TTL_SECONDS:
            self._bill_artifacts.pop((provider, property_key, kind), None)
            return None
        # Reaproveitar exige PROVA de que é a mesma fatura: as duas referências
        # presentes e iguais. Antes bastava não haver contradição, e o silêncio
        # passava por permissão — um canal que devolve valor sem referência, ou uma
        # leitura ainda não confirmada, deixava o código do mês anterior valer como
        # o de agora. Sem prova, buscar de novo custa um job; errar custa um
        # pagamento.
        current = self._bill_reference.get((provider, property_key), "")
        if not current or cached.get("reference") != current:
            self._bill_artifacts.pop((provider, property_key, kind), None)
            return None
        return cached

    async def _fetch_pix_payload(self, property_key: str):
        """(payload, referência, falha). Nunca registra o payload em log."""
        result, failure = await self._run_poco_bill_action(POCO_PIX_ACTION, property_key)
        if failure:
            return "", "", failure
        reference = str(result.get("reference") or "").strip() or self._bill_reference.get(
            ("equatorial", property_key), ""
        )
        payload = ""
        for field in ("pix_payload", "payload", "pix", "copia_e_cola"):
            candidate = str(result.get(field) or "").strip()
            if candidate:
                payload = candidate
                break
        if not payload:
            artifact_id = self._artifact_id(result)
            if artifact_id:
                path, artifact_failure = await self._resolve_poco_artifact(artifact_id)
                if artifact_failure:
                    return "", reference, artifact_failure
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        payload = handle.read().strip()
                except OSError:
                    logger.warning("Não consegui ler o artefato do Pix", exc_info=True)
                finally:
                    self._release_artifact(artifact_id, path)
        if not payload:
            return "", reference, (
                "O Poco não devolveu o Pix desta fatura. Não vou inventar um código de pagamento."
            )
        # Pix copia e cola é texto. Link é caminho para iniciar pagamento, e o ROD
        # não inicia pagamento nenhum.
        if "http://" in payload.lower() or "https://" in payload.lower():
            return "", reference, (
                "O que voltou do portal não é um Pix copia e cola. Não vou enviar link de pagamento."
            )
        return payload, reference, None

    def _only_stale_reading(self, provider: str, property_key: str) -> bool:
        """A informação mais recente desta conta é leitura guardada?

        Estado desconhecido (nenhuma consulta nesta execução) NÃO conta: nesse caso
        o artefato é buscado ao vivo agora, e recusar seria inventar um problema.
        O que se recusa é o caso provado: já sei que só tenho leitura antiga.
        """
        return bool(self._bill_stale_only.get((provider, property_key)))

    async def _refuse_stale_payment(self, chat_id: int, provider: str, property_key: str, query):
        """Recusa o pagamento E conserta a tela que ofereceu o botão.

        O cartão na tela foi desenhado quando a leitura era ao vivo, então ele
        continua exibindo PIX e BOLETO depois de a leitura virar guardada. Só
        recusar por mensagem nova deixava o dono com dois botões que não podem
        funcionar, uma recusa sem nenhum botão e nenhum caminho até ATUALIZAR.
        Aqui a mensagem tocada passa a dizer a verdade e a oferecer só o que
        funciona.
        """
        keyboard = self._bill_keyboard(provider, property_key, payment=False)
        await self._send_bill_text(chat_id, BILL_STALE_ONLY_MESSAGE, keyboard)
        # E o cartão tocado deixa de oferecer o que não pode fazer. Sem isto, os
        # dois botões continuam ali convidando o próximo toque, e o dono aprende
        # que o bot recusa por teimosia em vez de ler que a leitura envelheceu.
        message_id = getattr(getattr(query, "message", None), "message_id", None)
        if message_id is not None:
            await self._replace_bill_message(
                chat_id,
                message_id,
                bill_screen.render_stale_refusal(
                    provider,
                    self._property_label(property_key),
                    "🟠 Esta leitura envelheceu. Toque em ATUALIZAR para eu buscar a fatura "
                    "de agora — não entrego código de pagamento de leitura guardada.",
                ),
                keyboard,
            )
        return None

    async def _send_bill_pix(self, chat_id: int, property_key: str, query=None):
        provider = "equatorial"
        label = self._property_label(property_key)
        if self._only_stale_reading(provider, property_key):
            return await self._refuse_stale_payment(chat_id, provider, property_key, query)
        if self._flight_in_progress(provider, property_key, "pix"):
            await self._send_bill_text(
                chat_id, f"⏳ Já estou buscando o Pix da Equatorial — {label}. Aguarde."
            )
            return None
        cached = self._fresh_artifact(provider, property_key, "pix")
        if cached:
            await self._deliver_pix(chat_id, property_key, cached["payload"], cached.get("reference", ""))
            return None
        (payload, reference, failure), _reused = await self._single_flight(
            provider, property_key, "pix", lambda: self._fetch_pix_payload(property_key)
        )
        if failure:
            # A recusa do Pix não é o fim da linha: o boleto costuma existir
            # justamente quando o Pix não aparece na tela. Mensagem sem botão
            # obrigava o dono a rolar a conversa ou digitar de novo.
            await self._send_bill_text(
                chat_id,
                failure,
                self._bill_keyboard(provider, property_key, pix=False, boleto=True),
            )
            return None
        self._bill_artifacts[(provider, property_key, "pix")] = {
            "payload": payload,
            "reference": reference,
            "captured_at": time.time(),
        }
        await self._deliver_pix(chat_id, property_key, payload, reference)
        return None

    async def _deliver_pix(self, chat_id: int, property_key: str, payload: str, reference: str):
        """Só o código, em bloco, para copiar com um toque. Nenhum link."""
        label = self._property_label(property_key)
        title = f"Pix copia e cola — Equatorial {label} — ref. {reference or 'indisponível'}"
        sent = await self._send_bill_text(
            chat_id,
            f"{title}\n<pre>{html.escape(payload)}</pre>",
            parse_mode="HTML",
        )
        if sent is None:
            # Sem HTML o payload ainda precisa chegar legível e copiável.
            await self._send_bill_text(chat_id, f"{title}\n\n{payload}")
        return None

    async def _fetch_boleto_file(self, property_key: str):
        """({caminho, referência, artifact_id}, falha)."""
        result, failure = await self._run_poco_bill_action(POCO_BOLETO_ACTION, property_key)
        if failure:
            return {}, failure
        reference = str(result.get("reference") or "").strip() or self._bill_reference.get(
            ("equatorial", property_key), ""
        )
        artifact_id = self._artifact_id(result)
        path, artifact_failure = await self._resolve_poco_artifact(artifact_id)
        if artifact_failure:
            return {"reference": reference}, artifact_failure
        return {"path": path, "reference": reference, "artifact_id": artifact_id}, None

    @staticmethod
    def _safe_bill_filename(provider: str, property_key: str, reference: str, extension: str = "pdf") -> str:
        """Nome construído pelo Pi, nunca o nome que veio do portal.

        Nome de arquivo remoto é entrada não confiável: serve para travessia de
        diretório e para vazar dado do cadastro no chat. O dono continua vendo um
        nome amigável porque ele é montado aqui, com dados que já estão na tela.
        """
        parts = [
            PROVIDER_LABELS.get(provider, str(provider or "").title()),
            Executor._property_label(property_key).replace(" ", "-"),
            str(reference or ""),
        ]
        cleaned = []
        for part in parts:
            safe = re.sub(r"[^0-9A-Za-z]+", "-", str(part)).strip("-")
            if safe:
                cleaned.append(safe)
        name = "_".join(cleaned) or "Boleto"
        safe_extension = re.sub(r"[^0-9A-Za-z]+", "", str(extension or "pdf")) or "pdf"
        return f"{name[:60]}.{safe_extension}"

    async def _send_bill_boleto(self, chat_id: int, property_key: str, query=None):
        provider = "equatorial"
        label = self._property_label(property_key)
        if self._only_stale_reading(provider, property_key):
            return await self._refuse_stale_payment(chat_id, provider, property_key, query)
        if self._flight_in_progress(provider, property_key, "boleto"):
            await self._send_bill_text(
                chat_id, f"⏳ Já estou buscando o boleto da Equatorial — {label}. Aguarde."
            )
            return None
        (info, failure), _reused = await self._single_flight(
            provider, property_key, "boleto", lambda: self._fetch_boleto_file(property_key)
        )
        path = (info or {}).get("path")
        reference = (info or {}).get("reference", "")
        if failure or not path:
            # Mesmo raciocínio do Pix: o botão que falhou sai, o outro fica.
            await self._send_bill_text(
                chat_id,
                failure or BILL_ARTIFACT_UNAVAILABLE_MESSAGE,
                self._bill_keyboard(provider, property_key, pix=True, boleto=False),
            )
            return None
        filename = self._safe_bill_filename(provider, property_key, reference)
        caption = f"📄 Boleto Equatorial — {label} — referência {reference or 'indisponível'}"
        try:
            with open(path, "rb") as handle:
                await self.app.bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    filename=filename,
                    caption=caption,
                )
        except Exception:
            logger.warning("Falha ao enviar o boleto pelo Telegram", exc_info=True)
            await self._send_bill_text(
                chat_id,
                "Não consegui entregar o boleto agora. Nenhum pagamento foi realizado; "
                "tente novamente em alguns minutos.",
                self._bill_keyboard(provider, property_key),
            )
        finally:
            # O Telegram já respondeu (sucesso ou erro): o PDF de pagamento não fica
            # no disco do Pi em nenhum dos dois casos.
            self._release_artifact((info or {}).get("artifact_id"), path)
        return None

    async def handle_bill_callback(self, chat_id: int, data: str, query):
        """Callbacks ``bill_*``: menu do imóvel, PIX, boleto e atualizar."""
        if chat_id != Config.ALLOWED_USER_ID:
            logger.warning("Callback de fatura bloqueado (chat não autorizado)")
            return None
        raw = str(data or "")
        parts = raw.split(":")
        action = parts[0][len("bill_"):] if parts[0].startswith("bill_") else ""
        if len(parts) >= 3:
            provider, property_key = parts[1], parts[2]
        elif len(parts) == 2:
            provider, property_key = "equatorial", parts[1]
        else:
            provider, property_key = "equatorial", "casa"

        if action == "menu":
            payload = self._bill_property_menu(property_key)
            await self._replace_bill_message(
                chat_id,
                getattr(getattr(query, "message", None), "message_id", None),
                payload["text"],
                payload.get("reply_markup"),
            )
            return None

        # PIX e BOLETO existem só para a energia hoje. Para qualquer outra
        # concessionária a recusa vem com VOLTAR: recusa sem botão era um beco sem
        # saída no meio da conversa.
        if action in ("pix", "boleto") and provider != "equatorial":
            await self._send_bill_text(
                chat_id, BILL_ACTION_UNAVAILABLE_MESSAGE, self._bill_back_keyboard()
            )
            return None

        # Sem esta rede, uma exceção inesperada subiria até o handler genérico do
        # main, que responde "Deu ruim aqui" com o texto do erro — exatamente o
        # detalhe técnico que esta tela não pode mostrar.
        try:
            if action == "refresh":
                if provider == "saneago":
                    return await self._saneago_bill_flow(chat_id, property_key, query=query)
                if provider == "equatorial":
                    return await self._equatorial_bill_flow(
                        chat_id, property_key, query=query, forced=True
                    )
                await self._send_bill_text(
                    chat_id, BILL_ACTION_UNAVAILABLE_MESSAGE, self._bill_back_keyboard()
                )
                return None
            if action == "pix":
                return await self._send_bill_pix(chat_id, property_key, query=query)
            if action == "boleto":
                return await self._send_bill_boleto(chat_id, property_key, query=query)
        except Exception:
            logger.exception("Falha inesperada no botão de fatura")
            await self._send_bill_text(
                chat_id, BILL_GENERIC_FAILURE_MESSAGE, self._bill_back_keyboard()
            )
            return None

        # Botão de uma versão anterior do bot, ou callback que eu não escrevi.
        # Sem VOLTAR, o dono ficava preso numa mensagem que só diz "não".
        logger.warning("Callback de fatura desconhecido")
        await self._send_bill_text(
            chat_id,
            "Não reconheci esse botão de fatura. Ele pode ser de uma mensagem antiga; "
            "toque em VOLTAR para abrir o menu de contas.",
            self._bill_back_keyboard(),
        )
        return None

    # ---------- MENU DE CONTAS ----------
    def _poco_heartbeat(self):
        if not Config.POCO_NODE_ENABLED:
            return None
        try:
            from jarvis.api.app import get_poco_service

            status = get_poco_service().status() or {}
        except Exception:
            logger.debug("Não consegui ler o heartbeat do Poco", exc_info=True)
            return None
        if not status.get("online"):
            return None
        heartbeat = status.get("heartbeat")
        return heartbeat if isinstance(heartbeat, dict) else None

    @staticmethod
    def _confirmed_bill_properties() -> Dict[str, List[str]]:
        try:
            stored = Persistence.get_state(BILL_STATE_KEY, {}) or {}
        except Exception:
            logger.debug("Não consegui ler os imóveis confirmados", exc_info=True)
            return {}
        if not isinstance(stored, dict):
            return {}
        clean: Dict[str, List[str]] = {}
        for provider, keys in stored.items():
            if isinstance(keys, list):
                clean[str(provider)] = [str(key) for key in keys if isinstance(key, str)]
        return clean

    def _remember_bill_property(self, provider: str, property_key: str) -> None:
        """Só uma leitura concluída prova que o imóvel existe no cofre do Poco."""
        current = self._confirmed_bill_properties()
        keys = current.setdefault(provider, [])
        if property_key in keys:
            return
        keys.append(property_key)
        try:
            Persistence.set_state(BILL_STATE_KEY, current)
        except Exception:
            logger.debug("Não consegui registrar o imóvel confirmado", exc_info=True)

    def _bills_menu(self) -> Dict[str, Any]:
        """Botões só para imóveis realmente confirmados.

        LIMITAÇÃO CONHECIDA: o heartbeat do Poco expõe apenas ``water_units`` e
        ``energy_units`` — contagens, sem os nomes das unidades. Não existe, hoje,
        como derivar a lista de imóveis do cofre, então o menu mostra o que uma
        consulta bem-sucedida já provou e diz em voz alta o que ainda não sabe, em
        vez de inventar cinco botões plausíveis.
        """
        heartbeat = self._poco_heartbeat()
        confirmed = self._confirmed_bill_properties()
        configured = set()
        if heartbeat:
            for field in ("water_properties", "energy_properties"):
                values = heartbeat.get(field, [])
                if isinstance(values, list):
                    configured.update(str(value) for value in values)
        properties = sorted(configured | {key for keys in confirmed.values() for key in keys})

        rows = []
        try:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        except ImportError:
            InlineKeyboardMarkup = InlineKeyboardButton = None  # type: ignore

        if InlineKeyboardButton is not None:
            for key in properties:
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"🏠 {self._property_label(key)}", callback_data=f"bill_menu:{key}"
                        )
                    ]
                )
            rows.append([InlineKeyboardButton("🔙 Menu Principal", callback_data="help")])
            reply_markup = InlineKeyboardMarkup(rows)
        else:
            reply_markup = None

        lines = ["🧾 CONTAS & FATURAS", ""]
        if properties:
            lines.append("Escolha o imóvel para consultar:")
        else:
            lines.append("Ainda não concluí nenhuma consulta, então não tenho imóvel confirmado.")
        if heartbeat:
            inventory = (
                f"O Poco reporta {int(heartbeat.get('energy_units') or 0)} unidade(s) de energia e "
                f"{int(heartbeat.get('water_units') or 0)} de água no cofre."
            )
            if not configured:
                inventory = inventory[:-1] + " — esta versão ainda chegou sem os nomes."
            lines.append(inventory)
        else:
            lines.append("O Poco não está reportando agora, então não sei o que está no cofre.")
        if not configured:
            lines.append("")
            lines.append(
                "Se um imóvel não aparecer, peça uma vez pelo nome — por exemplo "
                "conta de luz kitnet 01."
            )
        return {"text": "\n".join(lines), "reply_markup": reply_markup}

    def _bill_property_menu(self, property_key: str) -> Dict[str, Any]:
        """Dentro do imóvel: só a concessionária que está configurada."""
        heartbeat = self._poco_heartbeat()
        confirmed = self._confirmed_bill_properties()
        label = self._property_label(property_key)

        water = property_key in confirmed.get("saneago", [])
        energy = property_key in confirmed.get("equatorial", [])
        if heartbeat:
            water = water or property_key in heartbeat.get("water_properties", [])
            energy = energy or property_key in heartbeat.get("energy_properties", [])
            if not heartbeat.get("saneago_configured", True):
                water = False
            if not heartbeat.get("equatorial_configured", True):
                energy = False

        rows = []
        try:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        except ImportError:
            InlineKeyboardMarkup = InlineKeyboardButton = None  # type: ignore

        if InlineKeyboardButton is not None:
            provider_row = []
            if water:
                # Antes este botão mandava a FRASE "conta de agua casa" como
                # callback: ela dava a volta pelo roteador de texto, caía no
                # caminho genérico e a resposta vinha como mensagem nova, sem
                # botão nenhum. Agora entra no mesmo handler da energia e termina
                # num cartão com ATUALIZAR e VOLTAR.
                provider_row.append(
                    InlineKeyboardButton(
                        "💧 Água", callback_data=f"bill_refresh:saneago:{property_key}"
                    )
                )
            if energy:
                provider_row.append(
                    InlineKeyboardButton(
                        "⚡ Energia", callback_data=f"bill_refresh:equatorial:{property_key}"
                    )
                )
            if provider_row:
                rows.append(provider_row)
            # Trocar de imóvel exigia voltar ao menu de contas e entrar de novo.
            # Os vizinhos confirmados ficam a um toque daqui.
            siblings = [
                key
                for key in {k for keys in confirmed.values() for k in keys}
                if key != property_key
            ]
            row = []
            for key in bill_screen.limited(siblings):
                row.append(
                    InlineKeyboardButton(
                        f"🏠 {self._property_label(key)}", callback_data=f"bill_menu:{key}"
                    )
                )
                if len(row) == 2:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
            rows.append([InlineKeyboardButton("🔙 VOLTAR", callback_data="menu_contas")])
            reply_markup = InlineKeyboardMarkup(rows)
        else:
            reply_markup = None

        lines = [f"🧾 {label}", ""]
        if water and energy:
            lines.append("Água e energia confirmadas aqui. Toque para consultar agora.")
        elif energy:
            lines.append("Energia confirmada aqui. Toque para consultar agora.")
        elif water:
            lines.append("Água confirmada aqui. Toque para consultar agora.")
        else:
            lines.append(
                "Nenhuma concessionária confirmada para este imóvel agora. "
                "Peça pelo nome uma vez (conta de luz ou conta de água) para eu confirmar."
            )
        if water or energy:
            lines.append("A consulta pode levar alguns minutos e chega numa mensagem só.")
        return {"text": "\n".join(lines), "reply_markup": reply_markup}

    def _cancel_action(self, chat_id: int) -> str:
        if chat_id in self.pending_actions:
            self.pending_actions.pop(chat_id)
            return "🛑 Ação cancelada com sucesso."
        return "⚠️ Nenhuma ação pendente para cancelar."

    async def handle_network_callback(self, chat_id: int, data: str, query):
        """
        Trata callbacks 'net_xxx' vindos de automações.
        """
        parts = data.split("_")
        action = parts[1] # reg, block, ignore

        if action == "ignore":
            await query.edit_message_text("👁️ Dispositivo ignorado.")
            return

        if action == "block":
            ip = parts[2]
            self.pending_actions[chat_id] = {
                "intent": "network_block_device",
                "action": "block",
                "params": {"ip": ip, "confirmed": True},
            }
            await query.edit_message_text(
                f"Bloquear o dispositivo {ip} no AdGuard?\n\nDigite confirmar para executar ou cancelar para abortar."
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
                 await query.edit_message_text("❌ Não consegui identificar o MAC address para cadastro.")
                 return

            # Start Flow
            ContextEngine.save_context(chat_id, {
                "flow": {
                    "type": "network_register",
                    "step": "ask_name",
                    "data": {"ip": ip, "mac": mac}
                }
            })

            await query.edit_message_text(f"📝 *Cadastro de Dispositivo*\nIP: `{ip}`\nMAC: `{mac}`\n\nQual nome você quer dar para ele?")
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
