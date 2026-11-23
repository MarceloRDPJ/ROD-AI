"""
INTENT_ENGINE.PY — FUZZY MATCHING ENGINE (RAPIDFUZZ)
======================================================

- This is the SECOND engine called by router.py
- Runs AFTER rules.py (deterministic rule engine)
- Handles natural language variations via rapidfuzz.WRatio
- Do NOT add patterns here that need EXACT matching
  (those belong in rules.py)
"""

from typing import Dict
from rapidfuzz import process, fuzz
import re
from jarvis.nlp.time_parser import parse_time_command
from jarvis.config import Config
from jarvis.nlp.normalizer import normalize_text

# =============================================================================
# ENGINE HÍBRIDO (PHASE 1)
# =============================================================================

class HybridIntentEngine:
    def __init__(self):
        self.intent_patterns = {
            "reminder_set": [
                "lembra", "lembrete", "me lembra", "me avisa", "avisa",
                "nao deixa eu esquecer", "me recorda", "lembre", "me lembre",
                "lembrar de beber agua", "avisa a cada minuto", "criar lembrete",
                "lembrar de", "não esquecer de", "pode me lembrar", "agenda pra",
                "marca pra", "quero lembrar", "cria um aviso"
            ],
            "reminder_list": [
                "listar lembretes", "ver meus lembretes", "quais sao meus lembretes",
                "ver avisos", "mostrar lembretes", "lista de tarefas", "o que tenho pra hoje",
                "agenda", "compromissos", "mostra lembretes", "exibe lembretes", "lembretes ativos"
            ],
            "reminder_today": [
                "lembretes de hoje", "agenda de hoje", "o que tenho hoje", "tarefas de hoje",
                "meus compromissos hoje", "hoje", "minha agenda hoje"
            ],
            "reminder_overdue": [
                "lembretes atrasados", "tarefas atrasadas", "o que esta atrasado", "vencidos",
                "pendencias atrasadas", "overdue", "atrasados"
            ],
            "reminder_delete": [
                "cancelar lembrete", "apagar lembrete", "remover aviso",
                "deleta esse lembrete", "esquecer lembrete", "excluir tarefa",
                "remove lembrete", "cancela aviso", "apaga esse", "deleta lembrete", "tira esse lembrete"
            ],
            "network_scan": [
                "quem ta na rede", "quem esta conectado",
                "dispositivos conectados", "quem ta usando internet",
                "quem ta online", "verificar rede", "scan de rede",
                "scanear", "escanear", "escaniar", "scan", "ver rede",
                "mostra dispositivos", "lista devices", "dispositivos na wifi",
                "conectados na rede", "quem esta usando", "mostra quem ta"
            ],
            "network_status": [
                "status da internet", "internet ta on", "ping", "conexao",
                "internet caiu", "internet ta ruim", "tem internet",
                "monitorar internet", "verificar conexao", "status da rede",
                "estado da rede", "rede ta funcionando",
                "testar internet", "internet funcionando", "wifi ta ok",
                "conexao ta boa", "rede online"
            ],
            "network_speed": [
                "velocidade da internet", "speed", "speedtest", "teste de velocidade",
                "speed test", "speed teste", "sped teste", "sped treste",
                "teste da velocidade da internet", "teste velocidade internet",
                "internet ta lenta", "medir velocidade", "taxa de download",
                "testar velocidade", "velocidade da net", "net ta lenta",
                "download ta quanto", "upload ta quanto"
            ],
            "network_stats": [
                "estatisticas de rede", "stats adguard", "consumo de rede",
                "quem ta gastando internet", "top consumidores", "bloqueios adguard",
                "uso do adguard",
                "dados da rede", "relatorio da rede", "trafico de rede",
                "uso de dados", "quem ta baixando"
            ],
            "network_block_device": [
                "bloquear dispositivo", "bloquear ip", "cortar internet do",
                "bloqueia o ip", "travar internet",
                "bloqueia esse", "bloquear mac", "negar acesso",
                "bloqueia o celular", "cortar acesso do"
            ],
            "network_block_site": [
                "bloquear site", "bloquear dominio", "bloqueia o site",
                "proibir site", "nao deixar acessar",
                "bloquear url", "bloquear pagina", "bloqueia dominio",
                "nao quero ver", "impedir acesso a"
            ],
            "network_rename": [
                "mudar o nome do", "renomear", "renomear dispositivo", "chamar o dispositivo",
                "apelidar o ip", "alterar nome na rede", "editar o nome da", "editar nome",
                "mudar nome de", "trocar nome",
                "muda nome", "renomeia", "dar nome pra",
                "identificar como", "chamar o device de"
            ],
            "hydration_status": [
                "quantas aguas eu ja bebi", "status hidratacao", "meta de agua",
                "quanto eu bebi hoje", "contagem de agua",
                "quanto bebi", "status da agua", "minha hidratacao",
                "como ta minha hidratacao", "bebi quantas aguas"
            ],
            "hydration_log": [
                "bebi", "tomei agua", "mais um copo", "bebi agua", "tomei mais uma",
                "registra agua", "anota ai bebi",
                "vou beber", "agua", "hidratar", "copo de agua", "bebendo",
                "tomei", "ja bebi", "beber agua"
            ],
            "hydration_analytics": [
                "analise de hidratacao", "padroes de agua", "insights agua",
                "estatisticas agua", "como tenho bebido agua", "historico de agua",
                "graficos de agua", "media de agua", "comparativo agua",
                "semana de agua", "hidratacao semanal"
            ],
            "system_status": [
                "status da cpu", "uso da cpu", "memoria",
                "ram", "status do sistema", "como ta o sistema", "status",
                "status do pi", "status do raspi", "status raspberry",
                "statu do pi", "stauts do raspi", "temperatura do pi",
                "tempratura do pi", "como ta o pi", "como esta o raspberry",
                "tudo bem", "como você está", "saúde do sistema",
                "como ta o rasp", "diagnostico", "saude do sistema",
                "healthcheck", "desempenho", "como esta o sistema", "performance"
            ],
            "system_logs": [
                "logs do sistema", "ver logs", "log de erro", "mostrar logs", "ultimos eventos",
                "ultimos logs", "eventos do sistema", "registros",
                "historico de eventos", "log de atividades"
            ],
            "greet": [
                "oi", "ola", "bom dia", "boa tarde", "boa noite", "e ai",
                "fala ai", "opa", "salve", "beleza", "fala", "eae"
            ],
            "small_talk": [
                "kk", "uai", "aham", "to bebendo", "boa",
                "entendi", "ok", "certo", "show", "legal", "demorou", "ta bom", "blz"
            ],
            "identity_who": [
                "quem é você", "quem e voce", "qual seu nome",
                "me apresente", "quem e jarvis", "fala de voce", "apresentacao"
            ],
            "identity_capabilities": [
                "o que voce sabe fazer", "o que voce faz", "quais seus poderes",
                "me ajuda com o que",
                "suas habilidades", "capacidades", "o que consegue fazer",
                "funcoes", "para que serve"
            ],
            "help": [
                "ajuda", "comandos", "menu", "opcoes", "socorro",
                "comando", "o que posso perguntar", "duvida", "como funciona", "me orienta"
            ],
            # COMMAND LIST INTENT
            "command_list": [
                "lista de comandos", "todos os comandos", "lista comandos", "manual",
                "quais seus comandos", "o que posso falar", "comandos disponiveis",
                "mostra comandos", "exibe comandos", "comandos uteis",
                "o que fala", "verbos"
            ],
            "light_on": [
                "ligar a luz", "acender luz", "acenda a luz da sala", "ligar", "acender",
                "ligar a luz da sala", "ligar a luz do quarto", "ligar a luz da cozinha",
                "acende a luz", "liga a lampada", "acender lampada",
                "luz on", "aceso"
            ],
            "light_off": [
                "apagar luz", "desligar a luz", "apague a luz do quarto", "apagar", "desligar",
                "apagar a luz da sala", "apagar a luz do quarto", "apagar a luz da cozinha",
                "apaga a lampada", "desligar lampada", "luz off",
                "luz apagada", "desliga lampada"
            ],

            # ===== WAKE-ON-LAN (WOL) =====
            "wake_pc": [
                # Comandos diretos
                "ligar o pc",
                "ligar pc",
                "ligar computador",
                "ligar o computador",

                # Variações com "acordar"
                "acordar o pc",
                "acordar pc",
                "acordar computador",

                # Wake on LAN explícito
                "wake on lan",
                "wol",
                "wake pc",

                # Comandos naturais
                "liga o pc pra mim",
                "acorda o pc",
                "preciso que ligue o pc",
                "pode ligar o computador",

                # Com urgência
                "liga o pc agora",
                "liga o pc urgente",

                # Variações regionais (goiano)
                "bota o pc pra funcionar",
                "liga esse trem ai",  # (se referindo ao PC)

                # Novas variações
                "acorda pc",
                "ligar meu pc",
                "iniciar pc",
                "power on pc",
                "dar boot no pc",
                "manda ligar o pc"
            ],

            "pc_status": [
                "pc ta ligado",
                "pc esta ligado",
                "computador ta on",
                "pc ta online",
                "pc respondendo",
                "status do pc", "pc on", "pc ligado?",
                "computador ligado?", "o pc ta funcionando"
            ],

            # NOVOS INTENTS DE IDENTIDADE
            "identity_creator": [
                "quem te criou", "quem fez voce", "quem é seu criador",
                "quem programou voce", "quem desenvolveu voce",
                "quem é seu pai", "quem te fez", "quem é marcelo", "conhece o marcelo",
                "criador", "quem desenvolveu", "criador do jarvis",
                "quem criou o jarvis", "quem te programou"
            ],
            "identity_purpose": [
                "qual seu proposito", "pra que voce serve", "qual sua missao",
                "por que voce existe", "qual seu objetivo",
                "sua funcao", "utilidade", "qual sua funcao",
                "para que foi criado", "qual seu papel"
            ],
            "identity_tech": [
                "como voce funciona", "qual sua tecnologia", "como foi feito",
                "qual sua stack", "que linguagem voce usa",
                "como e feito", "stack tecnologica", "qual linguagem",
                "tecnologia usada", "como programa"
            ],
            # NOVOS INTENTS DE MENU
            "menu_rede": ["menu rede", "comandos rede", "ajuda rede", "menu_rede",
                          "ajuda de rede", "rede ajuda", "comandos da rede",
                          "funcoes de rede", "opcoes de rede"],
            "menu_agenda": ["menu agenda", "comandos lembretes", "ajuda lembretes", "menu_agenda",
                            "ajuda de agenda", "agenda ajuda", "comandos da agenda",
                            "funcoes de agenda", "opcoes de agenda"],
            "menu_agua": ["menu agua", "menu_agua", "menu hidratacao", "comandos hidratacao",
                          "ajuda hidratacao", "opcoes de agua", "funcoes de hidratacao"],
            "menu_automacoes": ["menu automacoes", "menu_automacoes",
                                "ajuda automacoes", "comandos automacoes", "automacoes ajuda",
                                "funcoes automacao", "opcoes automacao"],
            "menu_sistema": ["menu sistema", "comandos sistema", "ajuda sistema", "menu_sistema",
                             "ajuda de sistema", "sistema ajuda", "comandos do sistema",
                             "funcoes de sistema", "opcoes de sistema"],

            # AUTOMATION SPECIFICS
            "automation_list": ["listar automacoes", "ver automacoes", "automacoes ativas", "quais automacoes",
                                "mostrar automacoes", "exibir automacoes", "automacoes configuradas",
                                "lista de automacoes", "quais automacoes existem"],
            "automation_config": ["config automacoes", "configurar automacoes", "editar automacoes",
                                  "configurar automacao", "editar automacao", "criar automacao",
                                  "nova automacao", "alterar automacao"],

            # TOKEN USAGE & REPORT
            "token_usage": [
                "gastos do jarvis", "quanto gastei", "uso de tokens", "consumo de api",
                "quanto custou", "estatisticas de uso", "meus gastos", "tokens usados",
                "consumo do dia", "gastei hoje"
            ],
            "daily_report": [
                "relatorio diario", "resumo do dia", "relatorio do sistema",
                "como foi o dia", "resumo de hoje", "relatorio completo",
                "status geral", "panorama do dia"
            ],
            "unknown_queries": [
                "o que voce nao sabe", "perguntas sem resposta", "duvidas pendentes",
                "comandos desconhecidos", "o que nao entendi", "falhas de interpretacao",
                "queries falhas"
            ]
        }
        # Threshold: never higher than 80 to avoid false positives on generic text
        raw_threshold = int(Config.INTENT_CONFIDENCE_THRESHOLD * 100)
        self.similarity_threshold = min(raw_threshold, 80)

    def identify_intent(self, user_input: str) -> Dict:
        if not user_input or not isinstance(user_input, str):
             return {"intent": "unknown", "confidence": 0.0}
        user_input = normalize_text(user_input)

        # Exact-match intents only — requires word boundaries
        # Prevents "copa" matching "opa" (greet) via substring
        _EXACT_INTENTS = {"greet"}
        for intent in _EXACT_INTENTS:
            examples = self.intent_patterns.get(intent, [])
            for pattern in examples:
                if len(pattern) <= 5 and re.search(rf"\b{re.escape(pattern)}\b", user_input):
                    return {"intent": intent, "confidence": 0.95}
                if user_input == pattern:
                    return {"intent": intent, "confidence": 0.95}

        best_intent = "unknown"
        best_score = 0

        for intent, examples in self.intent_patterns.items():
            if intent in _EXACT_INTENTS:
                continue
            match_result = process.extractOne(user_input, examples, scorer=fuzz.WRatio, score_cutoff=self.similarity_threshold)
            if match_result:
                match, score, _ = match_result
                if score > best_score:
                    best_score = score
                    best_intent = intent

        if best_score >= self.similarity_threshold:
            return {"intent": best_intent, "confidence": best_score / 100.0}

        return {"intent": "unknown", "confidence": 0.0}

# Global instance
engine = HybridIntentEngine()

def detect_intent(text: str) -> Dict:
    """
    Detecta intenção usando similaridade (RapidFuzz).
    Mantém compatibilidade com chamadas existentes.
    """
    normalized = normalize_text(text)
    if _looks_like_reminder_request(normalized):
        return _parse_reminder(text)

    result = engine.identify_intent(text)
    intent = result["intent"]
    confidence = result.get("confidence", 0.0)

    def recognized(payload: Dict) -> Dict:
        """Preserva a confiança do fuzzy matcher para o gate do roteador."""
        payload.setdefault("confidence", confidence)
        payload.setdefault("source", "intent_engine")
        return payload

    if intent == "unknown":
        return _fallback()

    if intent == "reminder_set":
        return _parse_reminder(text)

    if intent == "network_rename":
        return _parse_rename(text)

    if intent == "hydration_log":
        return recognized({"intent": "hydration_log"})

    if intent == "hydration_analytics":
        return recognized({"intent": "hydration_analytics"})

    if intent == "reminder_list":
        return recognized({"intent": "reminder_list"})

    if intent == "reminder_today":
        return recognized({"intent": "reminder_today"})

    if intent == "reminder_overdue":
        return recognized({"intent": "reminder_overdue"})

    if intent == "network_status":
        return recognized({"intent": "network_status", "action": "check", "entity": "network"})

    if intent == "network_speed":
        return recognized({"intent": "network_speed", "action": "check", "entity": "network"})

    if intent == "network_stats":
        return recognized({"intent": "network_stats"})

    if intent == "network_block_device":
        # Extract IP
        ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', text)
        ip = ip_match.group(1) if ip_match else None
        return {"intent": "network_block_device", "params": {"ip": ip, "target": ip}}

    if intent == "network_block_site":
        # Extract Domain (simplistic)
        # remove "bloquear", "site", etc.
        clean = text.lower()
        for w in ["bloquear", "site", "acesso", "ao", "o", "a", "bloqueia"]:
            clean = clean.replace(w, "")
        domain = clean.strip()
        return {"intent": "network_block_site", "params": {"site": domain, "domain": domain}}

    if intent == "reminder_delete":
        # Tenta extrair ID ou termo de busca
        match = re.search(r'(\d+)', text)
        target_id = int(match.group(1)) if match else None
        return {
            "intent": "reminder_delete",
            "text": text,
            "params": {"target_id": target_id} # Fixed: put in params for executor
        }

    if intent == "command_list":
        return recognized({"intent": "command_list"})

    if intent == "token_usage":
        return recognized({"intent": "token_usage"})

    if intent == "daily_report":
        return recognized({"intent": "daily_report"})

    if intent == "unknown_queries":
        return recognized({"intent": "unknown_queries"})

    return recognized(result)


def _looks_like_reminder_request(text: str) -> bool:
    return bool(re.search(
        r"\b(?:me\s+)?(?:lembra|lembre|lembrar|lembrete|lbrete|lbrte|lmebra|lembar|avisa|avisar)\b",
        text,
        re.IGNORECASE,
    ))

# =============================================================================
# PARSERS ESPECÍFICOS
# =============================================================================

def _parse_rename(text: str) -> Dict:
    """
    Extrai IP/MAC e novo nome.
    Ex: "mudar o nome do 192.168.1.52 para PC marcelo"
    Ex: "renomear 192.168.1.54 por celular Marcelo"
    """
    # Extract IP
    ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', text)
    target = ip_match.group(1) if ip_match else None

    # Extract Name
    name = None
    if target:
        # Regex mais flexível para capturar o nome após preposições
        # Captura tudo após 'para', 'por', 'de', ou logo após o IP se não houver preposição
        # Ex: "renomear X para Y" -> Y
        # Ex: "renomear X por Y" -> Y
        # Ex: "renomear X de Y" -> Y (menos comum mas possível)
        match = re.search(r'(?:para|por|de)\s+(.+)$', text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
        else:
            # Fallback: Se não tem preposição, pega o que vem depois do IP
            parts = text.split(target)
            if len(parts) > 1:
                potential = parts[1].strip()
                if potential:
                    name = potential

    return {
        "intent": "network_rename",
        "action": "rename",
        "target": target,
        "name": name,
        "text": text,
        "params": {
            "target": target,
            "name": name,
            "text": text
        }
    }
def _parse_reminder(text: str) -> Dict:
    """
    Parser de lembrete em linguagem natural.
    """
    # Usa o parser temporal aprimorado
    time_data = parse_time_command(text)
    minutes = time_data["minutes"]
    recurrence = time_data["recurrence"]
    is_recurring = time_data["is_recurring"]
    target_date = time_data.get("target_date")
    interval_minutes = time_data.get("interval_minutes", 0)

    reminder_text = text

    priority = "normal"
    if re.search(r"\b(urgente|critico|crítico|emergencia|emergência)\b", reminder_text, re.IGNORECASE):
        priority = "urgent"
    elif re.search(r"\b(importante|alta prioridade|prioridade alta)\b", reminder_text, re.IGNORECASE):
        priority = "high"
    elif re.search(r"\b(baixa prioridade|sem pressa)\b", reminder_text, re.IGNORECASE):
        priority = "low"

    nag = True
    nag_interval_minutes = 1
    nag_match = re.search(r"(?:me cobra|insiste|fica me lembrando).*?(?:a cada|de)\s*(\d+)\s*(minutos|min|horas|h)", reminder_text, re.IGNORECASE)
    if nag_match:
        nag_interval_minutes = int(nag_match.group(1)) * (60 if nag_match.group(2).lower().startswith(("h", "hora")) else 1)

    category = None
    category_map = {
        "saude": ["saude", "saúde", "remedio", "remédio", "medico", "médico"],
        "financeiro": ["boleto", "conta", "pagar", "banco", "pix", "financeiro"],
        "trabalho": ["trabalho", "reuniao", "reunião", "cliente"],
        "igreja": ["igreja", "culto", "celula", "célula", "ipog"],
        "casa": ["casa", "lixo", "limpar", "comprar", "mercado"],
        "estudos": ["estudo", "estudar", "tarefa", "curso", "faculdade"],
    }
    lower_text = reminder_text.lower()
    for cat, words in category_map.items():
        if any(w in lower_text for w in words):
            category = cat
            break

    # 1. Remove palavras-chave da intenção
    keywords = engine.intent_patterns["reminder_set"]
    sorted_keywords = sorted(keywords, key=len, reverse=True)

    for rule in sorted_keywords:
        if rule in reminder_text.lower():
             reminder_text = re.sub(re.escape(rule), "", reminder_text, flags=re.IGNORECASE)

    reminder_text = re.sub(r"\b(urgente|critico|crítico|emergencia|emergência|importante|alta prioridade|prioridade alta|baixa prioridade|sem pressa)\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\b(insiste|me cobra|fica me lembrando|nao deixa eu esquecer|não deixa eu esquecer|ate eu confirmar|até eu confirmar|ate eu responder|até eu responder)\b", "", reminder_text, flags=re.IGNORECASE)

    # 2. Remove expressões de tempo (Cleaning agressivo)
    # Dias da semana
    weekdays = ["domingo", "segunda", "segunda-feira", "terca", "terça", "terça-feira", "quarta", "quarta-feira", "quinta", "quinta-feira", "sexta", "sexta-feira", "sabado", "sábado"]
    for day in weekdays:
        reminder_text = re.sub(rf"\b(?:no|na|em)?\s*{day}\b", "", reminder_text, flags=re.IGNORECASE)

    # Hoje/Amanhã/Daqui
    reminder_text = re.sub(r"\b(hoje|amanha|amanhã)\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\b(mais tarde|depois|mais de noite|mais a noite|mais à noite)\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\bdaqui a pouco\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\bdaqui (?:a )?[\d]+ (?:minuto|minutos|min|hora|horas|h)\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\ba cada\s+\d+\s*(?:minuto|minutos|min|hora|horas|h)\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\b\d+\s*(?:minuto|minutos|min|hora|horas|h)\b", "", reminder_text, flags=re.IGNORECASE)

    # Horários (às 14h, 12:30, etc)
    reminder_text = re.sub(r"\b(?:as|às|ás)\s+\d{1,2}(?:[:h]\d{2})?h?\b", "", reminder_text, flags=re.IGNORECASE)
    reminder_text = re.sub(r"\b\d{1,2}:\d{2}\b", "", reminder_text, flags=re.IGNORECASE)

    # Termos soltos
    for w in ["minuto", "minutos", "hora", "horas", "a cada", "cada", "todo dia", "todos os dias"]:
        reminder_text = re.sub(re.escape(w), "", reminder_text, flags=re.IGNORECASE)

    # Limpeza final
    reminder_text = reminder_text.strip()
    # Remove preposições de ligação que sobraram no início (ex: "de puxar" -> "puxar")
    reminder_text = re.sub(r"^(?:de|pra|para|que|o|a)\s+", "", reminder_text, flags=re.IGNORECASE)

    reminder_text = re.sub(r'\s+', ' ', reminder_text).strip(' .,-')

    action = "default"
    text_lower = text.lower()
    if "agua" in text_lower or "água" in text_lower or "beber" in text_lower:
        action = "hydration"

    return {
        "intent": "reminder_set",
        "action": "create_request",
        "confidence": 1.0,
        "text": reminder_text if reminder_text else "Lembrete",
        "params": {
            "text": reminder_text if reminder_text else "Lembrete",
            "minutes": minutes,
            "target_date": target_date,
            "repeat": is_recurring,
            "recurrence": recurrence,
            "interval_minutes": interval_minutes,
            "action_type": action,
            "priority": priority,
            "nag": nag,
            "nag_interval_minutes": nag_interval_minutes,
            "category": category,
            "raw_text": text,
        }
    }

def _extract_period(text: str) -> str:
    if "hoje" in text:
        return "daily"
    if "mes" in text or "mensal" in text:
        return "monthly"
    if "semana" in text:
        return "weekly"
    return "current"

def _fallback() -> Dict:
    return {
        "intent": "chat",
        "response": "Uai… pode falar melhor que eu tento entender 😄"
    }
