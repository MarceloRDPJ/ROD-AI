from jarvis.core.rules import apply_rules
from jarvis.nlp.intent_engine import detect_intent
from jarvis.core.brain import Brain
from jarvis.core.context import ContextEngine
from jarvis.nlp.normalizer import normalize_text
from jarvis.config import Config
from datetime import datetime, timezone
import logging
import re

logger = logging.getLogger("core.router")

brain = Brain()

FLOW_TIMEOUT_MINUTES = 10

# Intenções de gerenciamento que interrompem fluxos ativos
MANAGEMENT_INTENTS = {
    "reminder_list", "reminder_delete", "reminder_update",
    "reminder_today", "reminder_overdue",
    "hydration_control", "hydration_status", "hydration_update", "hydration_activate",
    "hydration_log_explicit",
    "token_usage", "daily_report", "unknown_queries",
}

# Consultas sem efeito colateral podem aceitar mais variação ortográfica.
# Ações que alteram estado continuam usando o threshold global mais rígido.
TYPO_TOLERANT_INTENTS = {
    "system_status", "network_status", "network_speed", "network_stats",
    "network_scan", "reminder_list", "reminder_today", "reminder_overdue",
    "hydration_status", "hydration_analytics", "help", "command_list",
    "menu_rede", "menu_agenda", "menu_automacoes", "menu_sistema",
    "daily_report",
}


def _extract_entities(text: str):
    entities = {}
    locations = ["sala", "quarto", "cozinha", "banheiro", "escritorio", "varanda", "garagem"]
    devices = ["luz", "lampada", "ventilador", "ar", "tv", "televisao", "som"]
    for loc in locations:
        if re.search(rf"\b{loc}\b", text, re.IGNORECASE):
            entities["location"] = loc
            break
    for dev in devices:
        if re.search(rf"\b{dev}\b", text, re.IGNORECASE):
            entities["device"] = dev
            break
    return entities


# Palavras que indicam pergunta/conversa (não comando)
_QUESTION_WORDS = {
    "o que", "como", "quando", "por que", "porque", "qual", "quais",
    "quem", "onde", "para que", "que e", "que é", "o q",
    "me explique", "me fale", "conte", "explique", "significa",
    "o que significa", "o que e", "o que é",
}

# Palavras que indicam comando de sistema
_COMMAND_WORDS = {
    "status", "ligar", "desligar", "ajuda", "help",
    "lembrete", "lembrar", "bloquear", "desbloquear",
    "ativar", "desativar", "pausar", "retomar", "cancelar",
    "confirmar", "listar", "criar", "editar", "mudar", "alterar",
    "apagar", "remover", "deletar", "velocidade", "scan",
    "quem ta na rede", "diagnostico", "logs",
}


def _is_question(text: str) -> bool:
    """Heurística: texto parece uma pergunta ou conversa?"""
    t = text.lower().strip()
    if any(t.startswith(w) for w in _QUESTION_WORDS):
        return True
    if t.endswith("?"):
        return True
    if len(t) < 4:
        return False
    return False


def _is_command(text: str) -> bool:
    """Heurística: texto parece um comando de sistema?"""
    t = text.lower().strip()
    for w in _COMMAND_WORDS:
        if w in t:
            return True
    return False


def _is_llm_status(text: str) -> bool:
    t = text.lower().strip()
    return t in {"llm", "status llm", "llm status", "status da llm", "status do llm"}


def _make_result(intent, action, entity, confidence, source, params=None, text=""):
    return {
        "intent": intent, "action": action, "entity": entity,
        "confidence": confidence, "source": source,
        "params": params or {}, "text": text,
    }


def _check_flow_timeout(chat_id: int, flow: dict, context: dict) -> bool:
    flow_ts = flow.get("timestamp") or context.get("timestamp")
    if not flow_ts:
        return False
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(flow_ts)).total_seconds()
        if elapsed > FLOW_TIMEOUT_MINUTES * 60:
            ContextEngine.save_context(chat_id, {"flow": None})
            return True
    except Exception:
        pass
    return False


async def route(text: str, chat_id: int = None):
    text = normalize_text(text)

    if _is_llm_status(text):
        return brain.get_llm_status_response()

    # ============================================================
    # 1. REGRAS DE SEGURANÇA & INTERRUPÇÃO DE FLUXO
    # ============================================================
    rule = apply_rules(text)

    if rule and rule["intent"] in MANAGEMENT_INTENTS:
        if chat_id:
            ContextEngine.save_context(chat_id, {"flow": None})
        return _make_result(
            rule["intent"], rule["action"], rule["entity"],
            1.0, "rule", rule.get("params", {}), text,
        )

    # ============================================================
    # 2. FLUXO ATIVO (com timeout)
    # ============================================================
    if chat_id:
        context = ContextEngine.get_context(chat_id)
        flow = context.get("flow")
        if flow:
            if _check_flow_timeout(chat_id, flow, context):
                flow = None
        if flow:
            return _make_result("flow_input", "handle_input", None, 1.0, "flow", {"text": text}, text)

    # ============================================================
    # 3. REGRAS DETERMINÍSTICAS (comandos claros)
    # ============================================================
    if rule:
        if rule["intent"] == "reminder_set" and rule.get("action") == "create":
            pass  # deixa o NLP/brain interpretar melhor
        else:
            return _make_result(
                rule["intent"], rule["action"], rule["entity"],
                1.0, "rule", rule.get("params", {}), text,
            )

    # ============================================================
    # 4. CLASSIFICAÇÃO LEVE (sem LLM) — sinaliza comandos explícitos
    # ============================================================
    if _is_command(text):
        logger.debug(f"Classificado como COMANDO (heurística): '{text[:50]}' → NLP")
        # cai no NLP abaixo

    # ============================================================
    # 5. NLP LOCAL (Intent Engine fuzzy) com gate de confiança
    # ============================================================
    nlp_intent = detect_intent(text)
    if nlp_intent and nlp_intent.get("intent") not in ("chat", "unknown"):
        confidence = nlp_intent.get("confidence", 0)
        required_confidence = (
            0.72 if nlp_intent.get("intent") in TYPO_TOLERANT_INTENTS
            else Config.INTENT_CONFIDENCE_THRESHOLD
        )
        if confidence >= required_confidence:
            if chat_id:
                ctx = ContextEngine.get_context(chat_id)
                entities = _extract_entities(text)
                recent_entities = ctx.get("entities", {}) if ContextEngine.has_recent_activity(chat_id) else {}
                nlp_intent["entities"] = entities or recent_entities
                if not entities and recent_entities:
                    nlp_intent["context_inherited"] = True
            return nlp_intent
        else:
            logger.debug(f"NLP baixa confiança ({confidence:.2f}): '{text[:40]}' → Brain")

    # ============================================================
    # 6. CÉREBRO (somente quando nenhuma skill local reconheceu a frase)
    # ============================================================
    if _is_question(text):
        logger.debug(f"Classificado como CONVERSA (heurística): '{text[:50]}'")
    return await brain.process_intent(text, chat_id=chat_id)
