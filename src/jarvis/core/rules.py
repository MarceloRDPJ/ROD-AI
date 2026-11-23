# -*- coding: utf-8 -*-
"""
RULES.PY — HIGH PRIORITY DETERMINISTIC RULE ENGINE
====================================================

- This is the FIRST engine called by router.py
- Runs BEFORE intent_engine.py (fuzzy matching via rapidfuzz)
- Handles EXACT matches, confirm/cancel, greetings, and commands
  that MUST work precisely (dangerous actions, regex extraction)
- Do NOT duplicate patterns already covered by intent_engine.py's
  fuzzy matching
- Returns None if no rule matches → router falls through to intent_engine.py
"""

from typing import Dict, Optional
import re


def apply_rules(text: str, structured_entities: Optional[Dict] = None) -> Optional[Dict]:
    """
    Analisa texto normalizado e retorna um intent estruturado
    ou None se nenhuma regra casar.
    """

    if not text or not isinstance(text, str):
        return None

    t = text.lower().strip()
    structured_entities = structured_entities or {}

    # =====================================================
    # CONFIRMAÇÃO / CANCELAMENTO DE AÇÃO (PASSO 10)
    # =====================================================
    if t in ("executar", "confirmar", "sim", "autorizar", "pode fazer"):
        return {
            "intent": "action_confirm",
            "action": "confirm",
            "entity": None,
            "confidence": 1.0,
        }

    if t in ("cancelar", "nao", "não", "abortar", "para", "pare"):
        return {
            "intent": "action_cancel",
            "action": "cancel",
            "entity": None,
            "confidence": 1.0,
        }

    # =====================================================
    # GREET
    # =====================================================
    if t in ("oi", "olá", "ola", "e ai", "eai", "fala jarvis"):
        return {
            "intent": "greet",
            "action": None,
            "entity": None,
            "confidence": 1.0,
        }

    # =====================================================
    # TOKEN USAGE & REPORT
    # =====================================================
    if t in ("gastos", "uso de tokens", "quantos tokens", "consumo de ia", "uso da ia"):
        return {
            "intent": "token_usage",
            "action": "check",
            "entity": "system",
            "confidence": 1.0,
        }

    if t in ("relatorio", "relatorio diario", "resumo do dia", "relatorio do dia", "me resuma o dia"):
        return {
            "intent": "daily_report",
            "action": "show",
            "entity": "system",
            "confidence": 1.0,
        }

    if t in ("queries desconhecidas", "o que nao sabe", "perguntas sem resposta", "duvidas pendentes"):
        return {
            "intent": "unknown_queries",
            "action": "list",
            "entity": "system",
            "confidence": 1.0,
        }

    # =====================================================
    # LISTA DE COMANDOS (NOVO)
    # =====================================================
    if t in ["lista de comandos", "lista comandos", "todos os comandos", "quais seus comandos", "manual de comandos"]:
        return {
            "intent": "command_list",
            "action": "list",
            "entity": "system",
            "confidence": 1.0
        }

    if t in ["help", "ajuda", "menu", "comandos", "opcoes", "opções"]:
        return {"intent": "help", "action": "show", "entity": "system", "confidence": 1.0}

    if "ajuda bloquear" in t:
        return {
            "intent": "chat",
            "action": "reply",
            "entity": "help",
            "params": {"response": "🚫 Para bloquear: `bloquear 192.168.0.15` ou `bloquear site youtube.com`. Ação sensível pede confirmação."},
            "confidence": 1.0,
        }

    if "ajuda renomear" in t:
        return {
            "intent": "chat",
            "action": "reply",
            "entity": "help",
            "params": {"response": "✏️ Para renomear: `renomear 192.168.0.15 para TV Sala`."},
            "confidence": 1.0,
        }

    if t == "agora nao":
        return {
            "intent": "chat",
            "action": "reply",
            "entity": "hydration",
            "params": {"response": "Beleza. Quando beber água, mande `bebi` ou `bebi 500ml`."},
            "confidence": 1.0,
        }

    # =====================================================
    # MENUS (ATALHOS & CALLBACKS) - PRIORIDADE SOBRE HELP/LEMBRETES
    # =====================================================
    # Callbacks usam underscores, comandos de voz usam espaços. Tratamos ambos.

    if "menu_rede" in t or "menu rede" in t or "opcoes de rede" in t:
        return {"intent": "menu_rede", "action": "show", "entity": "network", "confidence": 1.0}

    if "menu_agenda" in t or "menu agenda" in t or "menu lembrete" in t or "menu tarefas" in t:
        return {"intent": "menu_agenda", "action": "show", "entity": "reminder", "confidence": 1.0}

    if "menu_automacoes" in t or "menu automacoes" in t or "menu automações" in t or "ver automacoes" in t:
        return {"intent": "menu_automacoes", "action": "show", "entity": "automation", "confidence": 1.0}

    if "menu_sistema" in t or "menu sistema" in t or "opcoes do sistema" in t:
        return {"intent": "menu_sistema", "action": "show", "entity": "system", "confidence": 1.0}

    # =====================================================
    # AUTOMATION SPECIFICS
    # =====================================================
    if "listar automacoes" in t or "listar automações" in t or "ver automações" in t:
        return {"intent": "automation_list", "action": "list", "entity": "automation", "confidence": 1.0}

    if "config automacoes" in t or "configurar automacoes" in t:
        return {"intent": "automation_config", "action": "config", "entity": "automation", "confidence": 1.0}

    # =====================================================
    # SYSTEM LOGS
    # =====================================================
    if "logs do sistema" in t or "ver logs" in t or "log de erro" in t:
        return {"intent": "system_logs", "action": "read", "entity": "system", "confidence": 1.0}

    # =====================================================
    # SYSTEM STATUS
    # =====================================================
    if (
        "status da cpu" in t
        or "status do sistema" in t
        or "como esta o sistema" in t
    ):
        return {
            "intent": "system_status",
            "action": "check",
            "entity": "system",
            "confidence": 1.0,
        }

    # =====================================================
    # NETWORK STATUS & SPEED
    # =====================================================
    if structured_entities.get("ip") and re.search(
        r"\b(?:detalhes|investigar|investigue|identificar|identifique|quem e|quem eh)\b",
        t,
    ):
        return {
            "intent": "network_device_details",
            "action": "inspect",
            "entity": "network",
            "params": {"ip": structured_entities["ip"]},
            "confidence": 1.0,
        }

    if "velocidade da internet" in t or "teste de velocidade" in t or "speedtest" in t:
        return {
            "intent": "network_speed",
            "action": "check",
            "entity": "network",
            "confidence": 1.0,
        }

    if "status da internet" in t or "internet ta on" in t or "ping" in t or "conexao" in t:
        return {
            "intent": "network_status",
            "action": "check",
            "entity": "network",
            "confidence": 1.0,
        }

    # =====================================================
    # NETWORK RENAME
    # =====================================================
    # Captura "renomear X por Y" ou "renomear X para Y" ou "renomear X Y"
    # Ex: "Renomear 192.168.1.54 por celular Marcelo"
    rename_match = re.search(r"renomear\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(?:para|por|de)?\s*(.+)", t)
    if rename_match:
        ip = rename_match.group(1)
        name = rename_match.group(2).strip()
        return {
            "intent": "network_rename",
            "action": "rename",
            "entity": "network",
            "params": {
                "target": ip,
                "name": name,
                "text": t
            },
            "confidence": 1.0,
        }

    # =====================================================
    # CONTEXT QUERY (PASSO 6)
    # =====================================================
    if "mudou algo" in t or "teve mudanca" in t:
        return {
            "intent": "context_query",
            "action": "compare",
            "entity": "context",
            "params": {
                "mode": "compare",
                "minutes_ago": 60,
            },
            "confidence": 0.9,
        }

    if "isso e normal" in t or "esta normal" in t:
        return {
            "intent": "context_query",
            "action": "baseline",
            "entity": "context",
            "params": {
                "mode": "baseline",
            },
            "confidence": 0.9,
        }

    delete_match = re.search(r"(?:apagar|remover|esquecer|deletar|cancelar)\s+lembrete\s+(\d+)", t)
    if delete_match:
        return {
            "intent": "reminder_delete",
            "action": "delete",
            "entity": "reminder",
            "params": {"index": int(delete_match.group(1))},
            "confidence": 1.0,
        }

    update_match = re.search(r"(?:editar|mudar|alterar)\s+lembrete\s+(\d+)\s*(.*)", t)
    if update_match:
        index = int(update_match.group(1))
        payload = update_match.group(2).strip()
        # Remove conectivos comuns do início do payload (para, por, as, etc) se necessário
        # Mas vamos deixar o fluxo tratar
        return {
            "intent": "reminder_update",
            "action": "update",
            "entity": "reminder",
            "params": {
                "index": index,
                "modification": payload if payload else None
            },
            "confidence": 1.0,
        }

    if t in ("lembretes de hoje", "agenda de hoje", "o que tenho hoje", "tarefas de hoje"):
        return {
            "intent": "reminder_today",
            "action": "list",
            "entity": "reminder",
            "confidence": 1.0,
        }

    if t in ("lembretes atrasados", "tarefas atrasadas", "atrasados", "vencidos", "o que esta atrasado"):
        return {
            "intent": "reminder_overdue",
            "action": "list",
            "entity": "reminder",
            "confidence": 1.0,
        }

    if re.fullmatch(r"(?:editar|mudar|alterar)\s+(?:meus?\s+)?lembretes?", t):
        return {
            "intent": "reminder_update",
            "action": "update",
            "entity": "reminder",
            "params": {},
            "confidence": 1.0,
        }

    # =====================================================
    # HIDRATAÇÃO (SISTEMA CONSOLIDADO)
    # =====================================================

    # 1. Ativação
    if "ativar hidratação" in t or "ativar hidratacao" in t or "me lembre de beber agua" in t or "me lembre de beber água" in t:
         return {
            "intent": "hydration_activate",
            "action": "activate",
            "entity": "hydration",
            "confidence": 1.0,
        }

    # 2. Log de Consumo com Valor Específico
    log_match = re.search(r"(?:bebi|tomei|mais)\s*(\d+)\s*(ml|l)?", t)
    if log_match:
        amount = int(log_match.group(1))
        if log_match.group(2) == "l":
            amount *= 1000
        return {
            "intent": "hydration_log_explicit",
            "action": "log",
            "entity": "hydration",
            "params": {"amount": amount},
            "confidence": 1.0,
        }

    # 3. Log de Consumo Explícito (Sem valor -> Usa copo padrão)
    if any(x in t for x in [
        "bebi agua", "bebi água",
        "tomei agua", "tomei água",
        "mais um copo", "mais uma garrafa",
        "bebi", "ja foi", "já foi", "tomei", "feito", "manda"
    ]):
        return {
            "intent": "hydration_log_explicit",
            "action": "log",
            "entity": "hydration",
            "params": {"amount": None},
            "confidence": 1.0,
        }

    # 4. Controle (Pausa/Cancelamento)
    if re.search(r"(?:pausar|parar|cancelar|silenciar|interromper)\s+(?:hidratação|hidratacao|agua|água)", t):
        return {
            "intent": "hydration_control",
            "action": "control",
            "entity": "hydration",
            "params": {"command": t},
            "confidence": 1.0,
        }

    if re.search(r"(?:retomar|voltar)\s+(?:com\s+)?(?:a\s+)?(?:hidratação|hidratacao|agua|água)", t):
        return {
            "intent": "hydration_control",
            "action": "control",
            "entity": "hydration",
            "params": {"command": t},
            "confidence": 1.0,
        }

    # 6. Edição de Parâmetros
    # Ex: "corrigir meta para 5000", "meu copo agora é 300ml"
    update_match = re.search(r"(?:editar|corrigir|mudar|alterar|definir)\s+(?:meta|copo|total|tamanho|intervalo)(?:.*de\s+)?(?:agua|água|hidratação)?\s*(?:pra|para|é)?\s*(\d+)?", t)
    if update_match:
        val = update_match.group(1)
        return {
            "intent": "hydration_update",
            "action": "update",
            "entity": "hydration",
            "params": {"value": val, "text": t},
            "confidence": 1.0,
        }

    # =====================================================
    # LEMBRETES (CRIAÇÃO BÁSICA)
    # =====================================================
    if "lembra" in t or "lembrete" in t or "lbrete" in t or "lbrte" in t:
        return {
            "intent": "reminder_set",
            "action": "create",
            "entity": "reminder",
            "confidence": 0.8,
        }

    # Typos comuns (lmebra, lmembra, lembar)
    if re.search(r"\b(l[ea]mbra|lmebra|lembar)\b", t):
        return {
            "intent": "reminder_set",
            "action": "create",
            "entity": "reminder",
            "confidence": 0.7,
        }

    # =====================================================
    # BLOQUEIO DE DISPOSITIVO (PREPARADO – PASSO 9)
    # =====================================================
    ip = structured_entities.get("ip")
    if ip and re.search(r"\b(?:bloquear|bloqueia|cortar|negar)\b", t):
        return {
            "intent": "network_block_device",
            "action": "request",
            "entity": "network",
            "params": {"ip": ip, "target": ip},
            "requires_confirmation": True,
            "confidence": 1.0,
        }

    if re.search(r"\b(bloquear dispositivo|bloqueia esse dispositivo)\b", t):
        return {
            "intent": "network_block_device",
            "action": "request",
            "entity": "network",
            "requires_confirmation": True,
            "confidence": 0.95,
        }

    # =====================================================
    # BLOQUEIO DE SITE (ADGUARD – FUTURO)
    # =====================================================
    domain = structured_entities.get("domain")
    if domain and re.search(r"\b(?:bloquear|bloqueia|proibir|impedir)\b", t):
        return {
            "intent": "network_block_site",
            "action": "request",
            "entity": "network",
            "params": {"site": domain, "domain": domain},
            "requires_confirmation": True,
            "confidence": 1.0,
        }

    site_match = re.search(r"\b(?:bloquear|bloqueia)\s+(?:site\s+)?([a-z0-9.-]+\.[a-z]{2,})\b", t)
    if site_match:
        return {
            "intent": "network_block_site",
            "action": "request",
            "entity": "network",
            "params": {"site": site_match.group(1)},
            "requires_confirmation": True,
            "confidence": 0.95,
        }

    # =====================================================
    # NENHUMA REGRA CASOU
    # =====================================================
    return None
