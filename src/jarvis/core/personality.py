import random


class Personality:
    """Textos humanos do ROD sem prometer capacidades inexistentes."""

    IDENTITY_WHO = [
        "Sou o **ROD do Cerrado**, assistente pessoal do Marcelo. Rodo localmente em um Raspberry Pi e cuido de tarefas práticas pelo Telegram."
    ]
    IDENTITY_CREATOR = [
        "Fui criado pelo **Marcelo RDP** para atender esta casa com ferramentas simples e verificáveis."
    ]
    IDENTITY_PURPOSE = [
        "Meu trabalho é consultar o Pi e a rede, ajudar com o AdGuard, organizar lembretes e acompanhar hidratação. Quando não tenho uma skill, eu digo isso claramente."
    ]
    IDENTITY_CAPABILITIES = [
        (
            "O que consigo fazer hoje:\n\n"
            "• medir CPU, RAM, disco e temperatura do Pi;\n"
            "• executar ping, speedtest e descoberta da rede;\n"
            "• consultar o AdGuard e confirmar bloqueios;\n"
            "• criar e administrar lembretes;\n"
            "• registrar hidratação;\n"
            "• enviar Wake-on-LAN quando o PC estiver configurado.\n\n"
            "Não controlo sensores, eletrodomésticos ou outro aparelho."
        )
    ]
    IDENTITY_TECH_STACK = [
        "Funciono com Python 3.12, Telegram Bot API, regras locais, SQLite, FastAPI e Docker no Raspberry Pi 3B. Não uso IA generativa no atendimento."
    ]

    GREET = ["Opa, fala comigo.", "Tô por aqui. Manda.", "Fala, chefe. No que ajudo?"]
    SUCCESS = ["Beleza, missão cumprida. ✅", "Tá na mão.", "Combinado."]
    ERROR = [
        "Deu um erro na execução. Tenta de novo?",
        "Não consegui concluir. Posso tentar novamente.",
        "Algo falhou e ficou registrado nos logs.",
    ]
    FALLBACK = [
        "Não peguei bem. Você quer consultar o Pi, a rede ou seus lembretes?",
        "Essa eu não consigo responder com segurança. Posso verificar sistema, rede, AdGuard ou agenda.",
        "Não reconheci uma skill para isso. Tente abrir o menu.",
    ]
    THANKYOU = ["Imagina, chefe.", "Disponha!", "Tamo junto."]
    GOODBYE = ["Falou!", "Até mais!", "Inté! Não esquece de beber água."]
    JOKES = [
        "O que o ping falou para o servidor? Já volto, só dei um timeout.",
        "Qual o protocolo favorito do goiano? O IP do chão — o IP que cê pisa.",
    ]
    SMALL_TALK = {
        "kk": ["Rir é bom demais, né?", "Hahaha", "😄"],
        "uai": ["Uai, sô.", "Uai.", "Bão?"],
        "aham": ["Tô ouvindo.", "Pode falar.", "Certo."],
        "to bebendo": ["Boa! Vou marcar então.", "Isso aí, hidratação é importante."],
        "bom dia": ["Bom dia, chefe!", "Bão dia!"],
        "boa tarde": ["Boa tarde!", "Boa tarde! Tô por aqui."],
        "boa noite": ["Boa noite!", "Boa noite! Qualquer coisa, me chama."],
        "como voce esta": ["Tô bem, chefe. E você?", "Tudo certo por aqui."],
        "obrigado": ["Por nada!", "Disponha!"],
        "tchau": ["Até mais!", "Falou!"],
        "piada": ["Essa é boa:", "Deixa eu mandar uma:"],
        "tempo": ["Não tenho sensor de clima. Posso ajudar com o Pi, rede ou agenda."],
        "cafe": ["Café é combustível de dev."],
        "musica": ["Não tenho integração de música configurada."],
    }

    FLOW_REMINDER_ASK_META = "Certo. Vou te lembrar de beber água. Qual é sua meta diária de água, em ml?"
    FLOW_REMINDER_ASK_CUP = "Beleza. Meta definida. E qual é o tamanho do seu copo, em ml?"
    FLOW_REMINDER_CONFIRM = "Entendi: lembrete {recurrence}.\nTexto: {text}\nTempo: daqui a {minutes} minutos.\n\nConfirma? (Sim/Não)"
    FLOW_REMINDER_CANCEL = "Beleza, cancelei."
    FLOW_REMINDER_SAVED = "Pronto! Lembrete salvo: {text}."
    FLOW_REMINDER_SAVED_HYDRATION = "Lembrete de hidratação salvo.\nMeta: {meta} ml | Copo: {cup} ml\nA cada {minutes} minutos."
    FLOW_REMINDER_ASK_TIME = "Antes de salvar, me fala que horas você quer esse lembrete."
    FLOW_REMINDER_ASK_REPEAT = "É para uma vez só ou para repetir?"

    @staticmethod
    def get_response(category: str) -> str:
        options = getattr(Personality, category, None)
        if isinstance(options, list):
            return random.choice(options)
        return "Uai, não sei o que dizer."

    @staticmethod
    def get_small_talk(trigger: str) -> str:
        for key, responses in Personality.SMALL_TALK.items():
            if key in trigger.lower():
                return random.choice(responses)
        return "Beleza."

    @classmethod
    def get_contextual_fallback(cls, text: str) -> str:
        if not text:
            return random.choice(cls.FALLBACK)
        text_lower = text.lower()
        if any(word in text_lower for word in ["obrigado", "valeu", "brigado", "obrigada"]):
            return random.choice(cls.THANKYOU)
        if any(word in text_lower for word in ["tchau", "bye", "falou", "inté", "ate mais", "até mais", "flw"]):
            return random.choice(cls.GOODBYE)
        if any(word in text_lower for word in ["piada", "risada", "engracado", "engraçado", "haha", "kkk"]):
            return f"{random.choice(cls.SMALL_TALK['piada'])}\n\n{random.choice(cls.JOKES)}"
        return random.choice(cls.FALLBACK)
