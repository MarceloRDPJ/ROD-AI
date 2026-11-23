import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import re

from jarvis.database.persistence import Persistence
from jarvis.core.context import ContextEngine
from jarvis.core.personality import Personality
from jarvis.nlp.time_parser import parse_time_command, format_pt_br
from jarvis.config import Config

logger = logging.getLogger("core.flows")

class RemindersFlow:
    """
    Gerencia o fluxo interativo de criação de lembretes.
    """

    @staticmethod
    def start_flow(chat_id: int, params: Dict[str, Any]) -> str:
        """
        Inicia o fluxo de confirmação.
        Salva o estado temporário e retorna a primeira pergunta.
        """
        # Serialização de datetime para JSON (state)
        if params.get("target_date") and isinstance(params["target_date"], datetime):
            params["target_date"] = params["target_date"].isoformat()

        # Estrutura do estado do fluxo
        flow_state = {
            "type": "reminder_creation",
            "step": "confirm_details",
            "data": params,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Se for hidratação, perguntar meta
        if params.get("action_type") == "hydration":
            flow_state["step"] = "ask_meta"
            ContextEngine.save_context(chat_id, {"flow": flow_state})
            return Personality.FLOW_REMINDER_ASK_META

        # Validação de Tempo
        target_date_iso = params.get("target_date")
        minutes = params.get("minutes", 0)

        if not target_date_iso and minutes <= 0:
            flow_state["step"] = "awaiting_clarification"
            flow_state["missing_field"] = "time"
            ContextEngine.save_context(chat_id, {"flow": flow_state})
            return "Certo. Que horas (e dia) é pra lembrar?"

        # Validação de Texto
        text = params.get("text")
        if not text or text.lower() == "lembrete":
            flow_state["step"] = "awaiting_clarification"
            flow_state["missing_field"] = "text"
            ContextEngine.save_context(chat_id, {"flow": flow_state})
            return "E pra lembrar do quê exatamente?"

        # Tudo certo -> Confirmação
        flow_state["step"] = "confirmation"
        ContextEngine.save_context(chat_id, {"flow": flow_state})

        # Formatação
        if target_date_iso:
            dt = datetime.fromisoformat(target_date_iso)
            time_display = format_pt_br(dt)
        else:
            # Fallback (não deve acontecer com novo parser, mas por segurança).
            # Precisa ser consciente de fuso: o scheduler trata data ingênua
            # como UTC, o que adiantaria o lembrete em 3 horas.
            dt = datetime.now(Config.TZ) + timedelta(minutes=minutes)
            time_display = format_pt_br(dt)
            # Atualiza state com target_date calculado
            flow_state["data"]["target_date"] = dt.isoformat()
            ContextEngine.save_context(chat_id, {"flow": flow_state})

        extra = RemindersFlow._confirmation_extra(params)
        return (
            f"Beleza, vê se tá certo:\n\n"
            f"📅 {time_display}\n"
            f"📝 {text}\n"
            f"{extra}\n"
            f"Confirma?"
        )

    @staticmethod
    def _confirmation_extra(data: Dict[str, Any]) -> str:
        lines = []
        if data.get("priority") in ("urgent", "high"):
            lines.append("⚠️ Prioridade: urgente" if data.get("priority") == "urgent" else "⚠️ Prioridade: alta")
        if data.get("category"):
            lines.append(f"🏷️ Categoria: {data['category']}")
        if data.get("nag", True) and not data.get("repeat"):
            lines.append(f"🔁 Vou cobrar a cada {data.get('nag_interval_minutes', 1)} min até você concluir")
        if data.get("repeat"):
            lines.append("🔄 Recorrente")
        return "\n".join(lines)

    @staticmethod
    def handle_response(chat_id: int, text: str, context_data: Dict) -> str:
        """
        Processa a resposta do usuário dentro do fluxo.
        """
        if not text:
             return "Uai, não entendi nada."

        # Cancelamento Global dentro do fluxo
        stop_words = ["cancelar", "voltar", "reiniciar", "não", "nao", "cancel", "esquece", "deixa"]

        # Se for apenas "não" na confirmação, tratamos como cancelamento também.
        # Mas vamos verificar o step.

        flow = context_data.get("flow")
        if not flow or flow["type"] != "reminder_creation":
            return None

        step = flow["step"]
        data = flow["data"]

        # Cancelamento Explicito
        if text.lower().strip() in stop_words:
            context_data.pop("flow", None)
            ContextEngine.save_context(chat_id, {"flow": None})
            return "Fechado, cancelei tudo. Sem lembrete."

        # --- Passo: Clarificação (Tempo) ---
        if step == "awaiting_clarification" and flow.get("missing_field") == "time":
            time_data = parse_time_command(text)

            if time_data["target_date"]:
                data["target_date"] = time_data["target_date"].isoformat()
                data["minutes"] = time_data["minutes"]
                data["repeat"] = time_data["is_recurring"] or data.get("repeat", False)
                data["recurrence"] = time_data["recurrence"]
                data["interval_minutes"] = time_data.get("interval_minutes", data.get("interval_minutes", 0))

                # Se ainda faltar texto
                if not data.get("text") or data.get("text").lower() == "lembrete":
                    flow["step"] = "awaiting_clarification"
                    flow["missing_field"] = "text"
                    flow["data"] = data
                    ContextEngine.save_context(chat_id, {"flow": flow})
                    return "E qual é o assunto do lembrete?"

                # Avança para confirmação
                flow["step"] = "confirmation"
                flow["data"] = data
                flow.pop("missing_field", None)

                ContextEngine.save_context(chat_id, {"flow": flow})

                dt = time_data["target_date"]
                time_display = format_pt_br(dt)

                return (
                    f"Beleza, vê se tá certo:\n\n"
                    f"📅 {time_display}\n"
                    f"📝 {data.get('text')}\n"
                    f"{RemindersFlow._confirmation_extra(data)}\n"
                    f"Confirma?"
                )
            else:
                return "Entendi a hora não. Tenta 'sábado às 14h' ou 'daqui 10 minutos'."

        # --- Passo: Clarificação (Texto) ---
        if step == "awaiting_clarification" and flow.get("missing_field") == "text":
            data["text"] = text

            # Avança para confirmação
            flow["step"] = "confirmation"
            flow["data"] = data
            flow.pop("missing_field", None)
            ContextEngine.save_context(chat_id, {"flow": flow})

            # Recupera data
            if data.get("target_date"):
                dt = datetime.fromisoformat(data["target_date"])
                time_display = format_pt_br(dt)
            else:
                time_display = "horário indefinido (erro)"

            return (
                f"Beleza, vê se tá certo:\n\n"
                f"📅 {time_display}\n"
                f"📝 {text}\n"
                f"{RemindersFlow._confirmation_extra(data)}\n"
                f"Confirma?"
            )

        # --- Passo: Meta de Água ---
        if step == "ask_meta":
            match = re.search(r'(\d+)\s*(l|ml|litros?)?', text, re.IGNORECASE)

            if match:
                num = int(match.group(1))
                unit = match.group(2)

                if unit and unit.lower().startswith('l'):
                    num *= 1000

                data["meta_ml"] = num

                # Próximo passo: Copo
                flow["step"] = "ask_cup"
                flow["data"] = data
                ContextEngine.save_context(chat_id, {"flow": flow})
                return Personality.FLOW_REMINDER_ASK_CUP
            else:
                return "Entendi o número não. Tenta assim: 2000 ou 2 litros."

        # --- Passo: Tamanho do Copo ---
        if step == "ask_cup":
            match = re.search(r'(\d+)\s*(ml)?', text, re.IGNORECASE)

            if match:
                cup = int(match.group(1))
                data["cup_ml"] = cup

                # Finalizar
                return RemindersFlow.finalize_creation(chat_id, data)
            else:
                return "Qual tamanho do copo? Ex: 250."

        # --- Passo: Confirmação Genérica ---
        if step == "confirmation":
            if text.lower() in ["sim", "s", "ok", "pode", "confirmo", "beleza", "bora", "pode ser", "isso", "vambora"]:
                return RemindersFlow.finalize_creation(chat_id, data)
            return "Ainda estou esperando sua decisão. Confirme para salvar ou cancele para descartar."

        return "Uai, me perdi aqui. Vamos começar de novo?"

    @staticmethod
    def finalize_creation(chat_id: int, data: Dict) -> str:
        """
        Salva no banco e retorna mensagem final.
        """
        text = data.get("text")
        minutes = data.get("minutes", 0)
        repeat = data.get("repeat")
        action_type = data.get("action_type", "default")
        target_date_iso = data.get("target_date")
        interval_minutes = data.get("interval_minutes") or minutes

        # Meta dados
        meta = {}
        meta["priority"] = data.get("priority", "normal")
        meta["nag"] = bool(data.get("nag", True)) and not repeat
        meta["nag_interval_minutes"] = int(data.get("nag_interval_minutes") or 1)
        meta["category"] = data.get("category")
        meta["raw_text"] = data.get("raw_text")
        meta["recurrence"] = data.get("recurrence", "none")
        if action_type == "hydration":
            # GARANTIA: Apenas UMA tarefa de hidratação ativa por usuário.
            # Cancela todas as anteriores antes de criar a nova.
            existing_tasks = Persistence.get_tasks_by_action(chat_id, "hydration")
            for t in existing_tasks:
                Persistence.update_task_status(t['id'], 'cancelled')

            meta["meta_ml"] = data.get("meta_ml", 2000)
            meta["cup_ml"] = data.get("cup_ml", 250)
            # Garantir que texto tenha marcador se necessário, ou só confiar no action

        # Calcular next_run
        if target_date_iso:
             next_run = target_date_iso if isinstance(target_date_iso, datetime) else datetime.fromisoformat(target_date_iso)
        else:
             now = datetime.now(timezone.utc)
             next_run = now + timedelta(minutes=minutes)

        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=Config.TZ).astimezone(timezone.utc)
        else:
            next_run = next_run.astimezone(timezone.utc)

        task_type = "recurring" if repeat else "unique"

        Persistence.add_task(
            chat_id=chat_id,
            text=text,
            next_run=next_run,
            action=action_type,
            task_type=task_type,
            interval_minutes=interval_minutes if repeat else 0,
            meta=meta
        )

        # Limpa fluxo
        ContextEngine.save_context(chat_id, {"flow": None})

        # Formatação Final
        time_display = format_pt_br(next_run.astimezone(Config.TZ))

        if action_type == "hydration":
            return f"Hidratação configurada! Meta: {meta['meta_ml']}ml."
        else:
            return f"Combinado. Lembrete salvo para {time_display}."

    @staticmethod
    def _format_task_line(index: int, task: Dict) -> str:
        dt = datetime.fromisoformat(task['next_run'])
        meta = json.loads(task.get('meta') or '{}')
        tags = []
        if meta.get("priority") in ("urgent", "high"):
            tags.append("prioridade alta" if meta.get("priority") == "high" else "urgente")
        if meta.get("category"):
            tags.append(meta["category"])
        if meta.get("nag"):
            tags.append("insistente")
        suffix = f" _({' · '.join(tags)})_" if tags else ""
        return f"*{index}.* {format_pt_br(dt)}\n   ↳ {task['text']}{suffix}"

    @staticmethod
    def list_reminders(chat_id: int) -> str:
        """
        Lista lembretes ativos formatados humanamente.
        """
        tasks = Persistence.get_active_tasks(chat_id)

        if not tasks:
            return "Você não tem nenhum lembrete ativo no momento."

        response = "📝 *Seus Lembretes:*\n\n"

        for i, task in enumerate(tasks, 1):
            line = RemindersFlow._format_task_line(i, task)
            if task['type'] == 'recurring':
                line += "\n   ↳ Recorrente"
            response += line + "\n\n"

        return response

    @staticmethod
    def list_today(chat_id: int) -> str:
        now = datetime.now(timezone.utc).astimezone(Config.TZ)
        start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        tasks = Persistence.get_tasks_between(chat_id, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))
        if not tasks:
            return "Hoje não tem lembrete pendente."
        msg = "📅 *Lembretes de hoje*\n\n"
        for i, task in enumerate(tasks, 1):
            msg += RemindersFlow._format_task_line(i, task) + "\n\n"
        return msg

    @staticmethod
    def list_overdue(chat_id: int) -> str:
        tasks = Persistence.get_overdue_tasks(chat_id, datetime.now(timezone.utc))
        if not tasks:
            return "Nenhum lembrete atrasado agora."
        msg = "⚠️ *Lembretes atrasados*\n\n"
        for i, task in enumerate(tasks, 1):
            msg += RemindersFlow._format_task_line(i, task) + "\n\n"
        return msg

    @staticmethod
    def handle_reschedule_response(chat_id: int, text: str, context_data: Dict) -> str:
        flow = context_data.get("flow")
        if not flow or flow.get("type") != "reminder_reschedule":
            return None

        task_id = flow.get("data", {}).get("task_id")
        task = Persistence.get_task(task_id)
        if not task or task.get("chat_id") != chat_id:
            ContextEngine.save_context(chat_id, {"flow": None})
            return "Não encontrei esse lembrete para remarcar."

        time_data = parse_time_command(text)
        if not time_data.get("target_date"):
            return "Pra quando? Tenta `amanhã às 9h`, `hoje às 20h` ou `daqui 30 minutos`."

        target = time_data["target_date"]
        if target.tzinfo is None:
            target = target.replace(tzinfo=Config.TZ).astimezone(timezone.utc)
        else:
            target = target.astimezone(timezone.utc)

        Persistence.update_task_next_run_and_status(task_id, target, "active")
        Persistence.log_interaction(task_id, "reschedule", target.isoformat())
        ContextEngine.save_context(chat_id, {"flow": None})
        return f"📅 Remarquei *{task['text']}* para {format_pt_br(target.astimezone(Config.TZ))}."

    @staticmethod
    def delete_reminder(chat_id: int, index: int) -> str:
        """
        Apaga um lembrete pelo índice da lista.
        """
        tasks = Persistence.get_active_tasks(chat_id)

        if index < 1 or index > len(tasks):
            return f"Não achei o lembrete número {index} não. Dá um 'listar lembretes' pra conferir."

        task_to_delete = tasks[index - 1]
        Persistence.update_task_status(task_to_delete['id'], 'cancelled')

        return f"Pronto! Apaguei o lembrete: {task_to_delete['text']}."

    @staticmethod
    def update_reminder(chat_id: int, index: int, modification: str = None) -> str:
        """
        Atualiza (recria) um lembrete.
        """
        tasks = Persistence.get_active_tasks(chat_id)
        if index < 1 or index > len(tasks):
            return f"Não achei o lembrete número {index}."

        old_task = tasks[index - 1]

        if not modification:
             return f"Pra editar, me fala o que mudar. Ex: 'mudar lembrete {index} para 16h' ou 'alterar lembrete {index} para Comprar Pão'."

        # Tenta detectar se é mudança de horário
        time_data = parse_time_command(modification)

        new_text = old_task['text']
        new_next_run = None
        new_minutes = 0 # Não usamos mais minutes relativo no DB se for absolute, mas mantemos compatibilidade

        if time_data["target_date"]:
             # É uma mudança de horário
             new_next_run = time_data["target_date"].isoformat()
             # Mantém o texto antigo
             # Mas se o modification tiver texto extra?
             # Simplificação: Se detectou hora, muda SÓ a hora.
        else:
             # Assume que é mudança de texto
             # Remove palavras comuns se necessário
             new_text = modification

        # Se não mudou hora, mantém a antiga?
        # A antiga está no DB. Precisamos calcular.
        if not new_next_run:
             new_next_run = old_task['next_run']

        # Cancelar antigo
        Persistence.update_task_status(old_task['id'], 'cancelled')

        # Criar novo
        # Recalcular minutes delta se necessário (opcional)

        # Salva
        params = {
            "text": new_text,
            "target_date": new_next_run,
            "minutes": 0, # Ignorado se target_date existe
            "repeat": old_task['type'] == 'recurring',
            "action_type": old_task['action']
        }

        # Reutiliza finalize_creation para salvar e formatar resposta
        # Mas finalize_creation espera Dict não serializado em 'target_date' se for datetime...
        # Não, finalize_creation aceita string iso no 'target_date'.

        return RemindersFlow.finalize_creation(chat_id, params).replace("Combinado. Lembrete salvo", "Lembrete atualizado")
