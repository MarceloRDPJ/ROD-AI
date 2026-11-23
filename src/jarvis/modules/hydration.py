import logging
import random
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from jarvis.database.persistence import Persistence
from jarvis.core.context import ContextEngine
from jarvis.config import Config
from jarvis.core.utils import is_quiet_hours
from jarvis.core.telegram_safe import safe_reply_text, safe_send_message

logger = logging.getLogger("modules.hydration")

class HydrationModule:
    """
    Módulo de Hidratação — ROD do Cerrado
    (Sistema Consolidado - Modo Diário)
    """

    # ==================================================
    # GAMIFICATION & FUN MESSAGES
    # ==================================================

    GAMIFICATION_LEVELS = [
        (0, "🌵 Cacto do Cerrado"),
        (20, "🦎 Lagartixo Hidratado"),
        (40, "🐸 Sapo Cururu"),
        (60, "🐆 Onça Pintada"),
        (80, "🌊 Guardião das Águas"),
        (100, "👑 Rei do Araguaia")
    ]

    MOTIVATION_DRINK = [
        "Mana restaurada! 💧 (+{ml}ml)",
        "Potion de HP consumida! 🥤",
        "Aí sim! Subindo o XP da hidratação. 🆙",
        "Boa! O rim tá fazendo a festa. 🎉",
        "Isso! Água é o cheat code da vida real. 🎮",
        "Mandou bem! O radiador tá cheio. 🚗",
        "Gole tático executado com sucesso. 🫡"
    ]

    MOTIVATION_NUDGE = [
        "Psiu... Sua barra de mana tá baixa. Bebe água! 📉",
        "Ei player 1, hora de recarregar. 🔋",
        "Não deixa o personagem desidratar não, sô! 🕹️",
        "Ó o delay mental chegando... Bebe água pra evitar o lag! 📶",
        "Levanta, estica e hidrata. Combo triplo! 👊",
        "Bora! Missão secundária: Beber um copo d'água. 📜",
        "Uai, esqueceu de salvar o jogo? Não, esqueceu da água! 💾"
    ]

    MOTIVATION_GOAL_NEAR = [
        "Boss Battle à vista! Falta pouco pra meta! ⚔️",
        "Reta final! Só mais uns goles pra platinar o dia. 🏆",
        "Tá quase lá! O troféu de hidratação tá brilhando. ✨",
        "Falta tiquinho! Bora fechar essa quest. 📜"
    ]

    MOTIVATION_GOAL_HIT = [
        "🏆 ACHIEVEMENT UNLOCKED: Hidratação Máxima!",
        "Meta batida! Você zerou o game da água hoje. 🎮",
        "Aí sim! Nível 'Rei do Araguaia' atingido com sucesso. 👑",
        "Sensacional! Pode comemorar (com mais água se quiser). 🥳",
        "GG WP! Good Game, Well Played. Hidratação 10/10. 👌"
    ]

    @staticmethod
    def _get_key(chat_id: int) -> str:
        return f"hydration_state_{chat_id}"

    @staticmethod
    def _load_state(chat_id: int) -> Dict[str, Any]:
        default = {
            "active": False,
            "daily_goal_ml": 2500,
            "cup_size_ml": 250,
            "interval_minutes": 60,
            "consumed_today_ml": 0,
            "last_drink_at": None,
            "last_reminder_at": None,
            "quiet_hours": {"start": "22:00", "end": "08:00"},
            "last_reset_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
        }
        return Persistence.get_state(HydrationModule._get_key(chat_id), default)

    @staticmethod
    def _save_state(chat_id: int, state: Dict[str, Any]):
        Persistence.set_state(HydrationModule._get_key(chat_id), state)

    @staticmethod
    def _check_daily_reset(state: Dict[str, Any]) -> bool:
        """
        Verifica se virou o dia e reseta o contador.
        Retorna True se houve reset.
        """
        # Reset baseado no fuso horário local, não UTC
        now_local = datetime.now(timezone.utc).astimezone(Config.TZ)
        now_str = now_local.strftime("%Y-%m-%d")

        if state.get("last_reset_date") != now_str:
            state["consumed_today_ml"] = 0
            state["last_reset_date"] = now_str
            # Opcional: Enviar mensagem de bom dia se ativo?
            # Deixar para o primeiro trigger do dia.
            return True
        return False

    @staticmethod
    async def snooze_hydration(chat_id: int, query, minutes: int = 15):
        """Snooze hydration reminder by N minutes."""
        state = HydrationModule._load_state(chat_id)
        if not state or not state.get("active"):
            await safe_reply_text(query.message, "Hidratação não está ativa.")
            return
        from datetime import timedelta
        now = datetime.now(Config.TZ)
        state["last_reminder_at"] = (now + timedelta(minutes=minutes)).isoformat()
        HydrationModule._save_state(chat_id, state)
        await safe_reply_text(
            query.message,
            f"💤 Beleza, te lembro de novo em {minutes} min. Quando beber água, manda `bebi`.",
        )

    @staticmethod
    def activate_flow(chat_id: int) -> str:
        """
        Inicia o fluxo de ativação/configuração.
        """
        ContextEngine.save_context(chat_id, {
            "flow": {
                "type": "hydration_setup",
                "step": "ask_goal",
                "data": {}
            }
        })
        return "Bora configurar sua hidratação! 💧\n\nQual sua meta diária de água? (Ex: 3000ml ou 3 litros)"

    @staticmethod
    def handle_flow(chat_id: int, text: str, context: Dict) -> str:
        """
        Gerencia o fluxo de configuração (Setup).
        """
        flow = context.get("flow")
        step = flow.get("step")
        data = flow.get("data", {})
        t = text.lower().strip()

        if step == "ask_goal":
            # Tenta extrair número
            match = re.search(r'(\d+)', t)
            if not match:
                return "Não entendi o número. Digita só a quantidade, tipo '2500'."

            val = int(match.group(1))
            if "l" in t and val < 100: val *= 1000 # Correção simples para litros

            data["daily_goal_ml"] = val

            flow["step"] = "ask_cup"
            flow["data"] = data
            ContextEngine.save_context(chat_id, {"flow": flow})
            return f"Beleza, meta de {val}ml.\n\nE qual o tamanho do seu copo/garrafa? (Ex: 250ml)"

        elif step == "ask_cup":
            match = re.search(r'(\d+)', t)
            if not match:
                return "Qual o tamanho do copo? (Ex: 300)"

            val = int(match.group(1))
            data["cup_size_ml"] = val

            flow["step"] = "ask_interval"
            flow["data"] = data
            ContextEngine.save_context(chat_id, {"flow": flow})
            return f"Copo de {val}ml anotado.\n\nDe quanto em quanto tempo quer ser lembrado? (Em minutos, ex: 60)"

        elif step == "ask_interval":
            match = re.search(r'(\d+)', t)
            if not match:
                return "Diz aí os minutos, tipo '45'."

            val = int(match.group(1))
            min_val = Config.HYDRATION_MIN_INTERVAL_MINUTES
            if val < min_val: return f"Menos de {min_val} minutos é exagero, né? Escolhe um tempo maior."

            data["interval_minutes"] = val

            # Finaliza Setup
            state = HydrationModule._load_state(chat_id)
            now_local = datetime.now(timezone.utc).astimezone(Config.TZ)
            state.update({
                "active": True,
                "daily_goal_ml": data["daily_goal_ml"],
                "cup_size_ml": data["cup_size_ml"],
                "interval_minutes": data["interval_minutes"],
                "last_reset_date": now_local.strftime("%Y-%m-%d"),
                "consumed_today_ml": 0,
                # Resetar last_reminder para garantir que o primeiro check (imediato) não seja bloqueado
                "last_reminder_at": None
            })
            HydrationModule._save_state(chat_id, state)

            # Limpa fluxo
            ContextEngine.save_context(chat_id, {"flow": None})

            # Cria task de "Heartbeat" (Gatilho para o Scheduler)
            # Agenda para 10 segundos no futuro para dar tempo de feedback visual
            HydrationModule._ensure_trigger_task(chat_id, val, immediate=True)

            return (
                "Show! Hidratação ativada. 🚀\n\n"
                f"🎯 Meta: {state['daily_goal_ml']}ml\n"
                f"🥤 Copo: {state['cup_size_ml']}ml\n"
                f"⏰ Intervalo: {val} min\n\n"
                "Se beber antes eu lembrar, é só falar 'bebi' ou 'ok'."
            )

        return None

    @staticmethod
    def _ensure_trigger_task(chat_id: int, interval_minutes: int, immediate: bool = False):
        """
        Garante que existe uma tarefa no Persistence para acordar o Scheduler.
        """
        # Verifica se já existe task de hydration_check
        tasks = Persistence.get_tasks_by_action(chat_id, "hydration_check")

        if immediate:
            # Agenda para agora (pequeno delay para UX)
            next_run = datetime.now(timezone.utc) + timedelta(seconds=10)
        else:
            next_run = datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)

        if tasks:
            # Atualiza existente
            t = tasks[0]
            if t['status'] != 'active':
                Persistence.update_task_status(t['id'], 'active')

            # Só atualiza next_run se for immediate ou se a atual estiver muito longe?
            # Melhor forçar atualização para garantir sync
            Persistence.update_task_next_run(t['id'], next_run)

            # Atualiza intervalo na task também (meta field)
            meta = json.loads(t.get('meta', '{}'))
            meta['trigger_interval'] = interval_minutes
            Persistence.update_task_meta(t['id'], meta)

            # CRÍTICO: Atualiza também a coluna interval_minutes da tabela tasks
            # O Scheduler usa ESSA coluna para reagendar
            # Precisamos de um método no Persistence para isso ou recriar a task
            # Como add_task/update_task não expõe update de interval, vamos fazer via SQL direto
            # (Adicionando método ad-hoc no Persistence ou recriando)
            # Recriar é mais seguro.
            Persistence.update_task_status(t['id'], 'cancelled')
            Persistence.add_task(
                chat_id=chat_id,
                text="Hydration Check",
                next_run=next_run,
                action="hydration_check",
                task_type="recurring",
                interval_minutes=interval_minutes,
                status="active",
                meta={"trigger_interval": interval_minutes}
            )
        else:
            # Cria nova
            Persistence.add_task(
                chat_id=chat_id,
                text="Hydration Check",
                next_run=next_run,
                action="hydration_check",
                task_type="recurring",
                interval_minutes=interval_minutes, # Isso fará o scheduler reagendar automaticamente também
                status="active",
                meta={"trigger_interval": interval_minutes}
            )

    @staticmethod
    def log_intake(chat_id: int, amount: Optional[int] = None, manual: bool = True, explicit: bool = True) -> str:
        """
        Registra consumo.
        manual=True: Acionado por comando.
        explicit=True: Comando claro ("bebi"). False: Implícito ("ok").
        """
        state = HydrationModule._load_state(chat_id)
        if HydrationModule._check_daily_reset(state):
            HydrationModule._save_state(chat_id, state)

        if not state["active"]:
             return "Hidratação não está ativa. Diz 'ativar hidratação' pra começar."

        # Segurança para comandos implícitos ("ok")
        if not explicit:
            # Só aceita "ok" se houve lembrete recente (ex: 60 min)
            last_remind = state.get("last_reminder_at")
            if not last_remind:
                return "Ok o que? Se bebeu água, diz 'bebi'."

            lr_dt = datetime.fromisoformat(last_remind)
            diff = (datetime.now(timezone.utc) - lr_dt).total_seconds() / 60

            # Tolerância de 60 minutos após o último lembrete
            if diff > 60:
                 return "Ok o que? Se bebeu água, diz 'bebi'."

        cup = state["cup_size_ml"]
        add = amount if amount else cup

        state["consumed_today_ml"] += add
        state["last_drink_at"] = datetime.now(timezone.utc).isoformat()
        HydrationModule._save_state(chat_id, state)

        # Loga interação no DB para estatísticas futuras (opcional, mas bom pra histórico)
        # Podemos usar um task_id fictício ou 0, ou buscar o trigger task
        tasks = Persistence.get_tasks_by_action(chat_id, "hydration_check")
        if tasks:
            Persistence.log_interaction(tasks[0]['id'], "confirm", str(add))

        # Adicionar log ao histórico
        Persistence.log_hydration_intake(
            chat_id=chat_id,
            amount_ml=add,
            goal_ml=state["daily_goal_ml"],
            consumed_so_far_ml=state["consumed_today_ml"],
            manual=manual
        )

        # Feedback
        return HydrationModule._generate_feedback(state, add)

    @staticmethod
    def get_analytics(chat_id: int) -> str:
        """Retorna análise de padrões de hidratação"""
        from jarvis.modules.hydration_analytics import HydrationAnalytics

        analysis = HydrationAnalytics.analyze_patterns(chat_id)

        msg = "📊 **Análise de Hidratação (30 dias)**\n\n"
        msg += f"📈 Média diária: {analysis['average_daily_ml']}ml\n"
        msg += f"🎯 Taxa de sucesso: {analysis['goal_completion_rate']}%\n"

        if analysis['streak_days'] > 0:
            msg += f"🔥 Sequência atual: {analysis['streak_days']} dias\n"

        if analysis['peak_hours']:
            hours_str = ", ".join([f"{h}h" for h in analysis['peak_hours']])
            msg += f"⏰ Horários de pico: {hours_str}\n"

        msg += "\n💡 **Insights:**\n"
        for suggestion in analysis['suggestions']:
            msg += f"• {suggestion}\n"

        return msg

    @staticmethod
    def _get_level_title(percent: int) -> str:
        """Retorna o título baseado na porcentagem"""
        title = "Iniciante"
        for threshold, t in HydrationModule.GAMIFICATION_LEVELS:
            if percent >= threshold:
                title = t
        return title

    @staticmethod
    def _generate_feedback(state: Dict, added: int) -> str:
        current = state["consumed_today_ml"]
        goal = state["daily_goal_ml"]
        percent = int((current / goal) * 100) if goal > 0 else 0
        level = HydrationModule._get_level_title(percent)

        if current >= goal:
            # Se acabou de bater
            if current - added < goal:
                 return random.choice(HydrationModule.MOTIVATION_GOAL_HIT) + f"\n\nNível atual: {level}"
            else:
                 return f"Mais {added}ml! Total: {current}ml (Meta já batida! 🏆)\nNível: {level}"

        elif current >= (goal * 0.8):
             base = random.choice(HydrationModule.MOTIVATION_GOAL_NEAR)
             return f"{base}\nXP: {current}/{goal}ml ({percent}%)"

        else:
             base = random.choice(HydrationModule.MOTIVATION_DRINK).format(ml=added)
             return f"{base}\nXP: {current}/{goal}ml ({percent}%) | Rank: {level}"

    @staticmethod
    def _generate_reminder_message(chat_id: int, cup_ml: int, now: datetime) -> str:
        """
        Gera a mensagem do lembrete baseada no contexto (inércia, horário).
        """
        # Load state purely to get percent if needed, but here we just need a prompt
        state = HydrationModule._load_state(chat_id)
        current = state["consumed_today_ml"]
        goal = state["daily_goal_ml"]
        percent = int((current / goal) * 100) if goal > 0 else 0
        level = HydrationModule._get_level_title(percent)

        message_type = "normal"
        # Increase nudge chance if behind schedule? For now random.
        if random.random() < 0.3:
             message_type = "nudge"

        if message_type == "nudge":
             base = random.choice(HydrationModule.MOTIVATION_NUDGE)
             return f"{base}\n(Rank atual: {level})"
        else:
             hour = (now.hour - 3) % 24
             if hour < 12:
                 greeting = "Bom dia, jogador!"
             elif hour < 18:
                 greeting = "Segue o jogo!"
             else:
                 greeting = "Noite de grind."

             return f"💧 Quest Ativa: Beber {cup_ml}ml.\n{greeting}\n\nStatus: {percent}% ({level})"

    @staticmethod
    def get_status_message(chat_id: int) -> str:
        state = HydrationModule._load_state(chat_id)
        HydrationModule._check_daily_reset(state)

        if not state["active"]:
            return "Hidratação desativada. Use 'ativar hidratação'."

        total = state["consumed_today_ml"]
        goal = state["daily_goal_ml"]

        percentage = int((total / goal) * 100)
        percentage = min(100, percentage)
        bars = "🟦" * (percentage // 10) + "⬜" * (10 - (percentage // 10))

        next_msg = ""
        # Calcula próximo lembrete estimado
        if state["last_drink_at"]:
            last = datetime.fromisoformat(state["last_drink_at"])
            interval = state["interval_minutes"]
            next_run = last + timedelta(minutes=interval)

            # Ajuste fuso horário local via Config
            next_local = next_run.astimezone(Config.TZ)
            next_str = next_local.strftime("%H:%M")
            next_msg = f"\n⏰ Próximo gole: ~{next_str}"

        return (
            f"💧 *Status Hidratação*\n\n"
            f"{bars} {percentage}%\n"
            f"Total: {total}ml / {goal}ml\n"
            f"Falta: {max(0, goal - total)}ml{next_msg}"
        )

    @staticmethod
    def update_config(chat_id: int, params: Dict[str, Any]) -> str:
        state = HydrationModule._load_state(chat_id)
        if not state["active"]:
            return "Hidratação não está ativa."

        text = params.get("text", "").lower()
        val = params.get("value")

        # Tenta extrair se não veio
        if not val:
             match = re.search(r'(\d+)', text)
             if match: val = int(match.group(1))

        if not val:
            return "Preciso do valor. Ex: 'mudar meta para 3000'."

        val = int(val)
        changed = False
        msg = ""

        if "meta" in text or "total" in text:
            if val < 100 and "l" in text: val *= 1000
            state["daily_goal_ml"] = val
            msg = f"Meta atualizada para {val}ml."
            changed = True
        elif "copo" in text or "tamanho" in text:
            state["cup_size_ml"] = val
            msg = f"Copo atualizado para {val}ml."
            changed = True
        elif "intervalo" in text or "tempo" in text:
            min_val = Config.HYDRATION_MIN_INTERVAL_MINUTES
            if val < min_val: return f"Intervalo muito curto. Mínimo {min_val} min."
            state["interval_minutes"] = val
            msg = f"Intervalo atualizado para {val} min."
            changed = True
            # Atualiza trigger task
            HydrationModule._ensure_trigger_task(chat_id, val)
        else:
            # Heurística
            if val > 1000:
                state["daily_goal_ml"] = val
                msg = f"Assumi que é a Meta: {val}ml."
                changed = True
            elif val < 1000:
                state["cup_size_ml"] = val
                msg = f"Assumi que é o Copo: {val}ml."
                changed = True

        if changed:
            HydrationModule._save_state(chat_id, state)
            return msg

        return "Não entendi o que atualizar."

    @staticmethod
    def control_hydration(chat_id: int, command: str) -> str:
        state = HydrationModule._load_state(chat_id)
        command = command.lower()

        if "pausar" in command or "silencio" in command or "parar" in command:
            if not state["active"]: return "Já está parada."
            # Pausar = Active False mas manter dados?
            # Requirement: "Pausar: mantém estado, suspende notificações"
            state["active"] = False
            HydrationModule._save_state(chat_id, state)
            return "Hidratação pausada. Pra voltar: 'retomar hidratação'."

        elif "retomar" in command or "voltar" in command:
            if state["active"]: return "Já está ativa."
            state["active"] = True
            HydrationModule._save_state(chat_id, state)
            return "Hidratação retomada! Foco na meta. 💧"

        elif "cancelar" in command:
             state["active"] = False
             HydrationModule._save_state(chat_id, state)
             # Opcional: deletar trigger task
             return "Hidratação cancelada."

        return "Comando desconhecido."

    @staticmethod
    async def check_schedule(app, task: Dict[str, Any]):
        """
        Método chamado pelo SchedulerService quando a task 'hydration_check' vence.
        """
        chat_id = task['chat_id']
        state = HydrationModule._load_state(chat_id)

        # 1. Reset Diário (Crucial)
        HydrationModule._check_daily_reset(state)

        # 2. Se não ativa, ignora (mas mantém task rodando? Ou pausa task?)
        if not state["active"]:
            # Se a hidratação foi pausada no estado, pausamos a task também para economizar recursos
            Persistence.update_task_status(task['id'], 'paused')
            return

        # 3. Quiet Hours
        now = datetime.now(timezone.utc)
        local_now = now.astimezone(Config.TZ)

        # Lógica centralizada de Quiet Hours
        # Usa configuração do estado ou fallback
        q_hours = state.get("quiet_hours", {"start": "22:00", "end": "08:00"})

        if is_quiet_hours(local_now, q_hours):
            # Não notifica.
            # Reagenda para o fim do quiet hours?
            # Ou apenas deixa o scheduler padrão (recorrência) rodar?
            # Se deixarmos padrão, ele checa a cada X min.
            # Melhor: Deixar rodar. Se o usuário acordar e beber, ele loga.
            # O scheduler só não manda msg.
            HydrationModule._save_state(chat_id, state) # Salva reset se houve
            return

        # 4. Verifica Intervalo desde último gole
        interval = state["interval_minutes"]
        last_drink = state.get("last_drink_at")

        should_remind = False

        if not last_drink:
            should_remind = True # Nunca bebeu (ou resetou e null?)
            # Se resetou dia, last_drink mantém do dia anterior?
            # No reset diário, não limpamos last_drink, apenas consumed.
            # Mas se last_drink for de ontem, o diff será grande -> Trigger.
            # Correto.
        else:
            last_dt = datetime.fromisoformat(last_drink)
            diff = (now - last_dt).total_seconds() / 60
            if diff >= interval:
                should_remind = True

        # Anti-Spam: Verifica último lembrete
        last_remind = state.get("last_reminder_at")
        if last_remind:
            lr_dt = datetime.fromisoformat(last_remind)
            diff_remind = (now - lr_dt).total_seconds() / 60
            # Se já lembrou recentemente (menos que o intervalo), não lembra de novo
            if diff_remind < interval:
                should_remind = False

        if should_remind:
            # Envia Lembrete
            msg = HydrationModule._generate_reminder_message(chat_id, state["cup_size_ml"], now)

            try:
                # Botões
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                kb = [[InlineKeyboardButton("✅ Bebi", callback_data="bebi agua"), InlineKeyboardButton("💤 +15min", callback_data="agora nao")]]
                await safe_send_message(app.bot, chat_id, msg, reply_markup=InlineKeyboardMarkup(kb))

                state["last_reminder_at"] = now.isoformat()
                HydrationModule._save_state(chat_id, state)

            except Exception as e:
                logger.error(f"Erro envio hidratação: {e}")
