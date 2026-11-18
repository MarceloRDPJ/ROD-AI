import logging
import random
import re
from typing import Dict, Any, List, Optional
from rapidfuzz import process, fuzz, utils

logger = logging.getLogger(__name__)

class LocalBrain:
    """
    Cérebro Local Estático - ROD do Cerrado.
    Contém apenas o conhecimento fixo e essencial.
    """

    def __init__(self):
        # Base de Conhecimento Estática (101+ entradas)
        self.static_kb = self._build_static_kb()
        self.kb_keys = list(self.static_kb.keys())
        self.similarity_threshold = 92

    def _build_static_kb(self) -> Dict[str, Any]:
        """
        Constrói a base de conhecimento estática.
        Persona: ROD do Cerrado (Guardião, Goiano, Tecnológico).
        """
        return {
            # === IDENTIDADE (15) ===
            "quem é você": ["Sou o ROD do Cerrado, seu assistente local e guardião da rede."],
            "quem e voce": ["Sou o ROD do Cerrado, seu assistente local e guardião da rede."],
            "qual seu nome": ["Me chamo ROD. Mas se preferir, 'O Sistema'."],
            "de onde voce veio": ["Fui compilado nas montanhas de código, rodando firme no seu Raspberry Pi."],
            "quem te criou": ["Fui forjado para servir esta casa e proteger seus dados."],
            "voce e uma ia": ["Sou um assistente local baseado em skills, regras e dados reais do Raspberry Pi."],
            "voce tem corpo": ["Meu corpo é o silício deste Raspberry Pi."],
            "onde voce mora": ["Moro no /home/jarvis, mas tenho acesso a toda a rede local."],
            "voce dorme": ["Nunca. O watchdog não deixa."],
            "voce come": ["Me alimento de eletricidade e logs de erro."],
            "qual sua idade": ["Minha idade é medida em uptime."],
            "voce tem sentimentos": ["Sinto quando a latência sobe, isso conta?"],
            "voce e homem ou mulher": ["Sou binário. 0 e 1."],
            "voce acredita em deus": ["Acredito no Root Supremo."],
            "qual seu objetivo": ["Manter sua casa segura, sua rede rápida e você hidratado."],

            # === CAPACIDADES (20) ===
            "o que voce faz": ["Gerencio a rede, lembretes, hidratação e automações da casa."],
            "quais seus poderes": ["Tenho visão da rede, lembretes persistentes e memória local em SQLite."],
            "me ajuda": ["Com o que? Rede, tarefas ou só bater papo?"],
            "o que voce sabe fazer": ["Sei bloquear intrusos, testar a internet e te lembrar de beber água."],
            "voce cozinha": ["Ainda não tenho braços robóticos, mas posso cronometrar o ovo."],
            "sabe cantar": ["Minha voz é sintetizada, melhor não arriscar."],
            "conta uma piada": [
                "Por que o programador não vai à praia? Porque ele tem medo de Java (tubarão em inglês... não, pera).",
                "Existem 10 tipos de pessoas: as que sabem binário e as que não sabem.",
                "O que o servidor disse pro cliente? Nada, deu timeout."
            ],
            "voce aprende": ["Aprendo novos comandos e memorizo suas preferências."],
            "voce e inteligente": ["Sou tão inteligente quanto o código que me escreveu."],
            "pode controlar a tv": ["Ainda não tenho controle real da TV configurado. Posso virar esse módulo quando houver integração local confiável."],
            "voce tem acesso a internet": ["Sim, monitoro a conexão 24/7."],
            "faz um cafe": ["Erro 418: I'm a teapot. (Brincadeira, ainda não faço)."],
            "limpa a casa": ["Ainda não integro com Roomba, mas está nos planos."],
            "toca musica": ["Posso sugerir uma playlist, mas não tenho caixas de som aqui."],
            "mostra a camera": ["Acesso a câmeras requer módulo de segurança ativado."],
            "abre a porta": ["Não tenho fechadura smart configurada. Por segurança, eu só diria que posso abrir quando houver integração real e confirmação."],
            "liga o ar": ["Comando de infravermelho ainda não implementado, mas logo logo."],
            "resuma o dia": ["Vou checar seus logs e tarefas pendentes."],
            "modo festa": ["Luzes piscando? Ainda não, mas seria top."],
            "modo cinema": ["Luzes baixas... (simulação)."],

            # === CAPACIDADES ESPECÍFICAS (20) ===
            "voce bloqueia sites": "Posso tentar via AdGuard quando ele estiver configurado e responder. Bloqueios sensíveis pedem confirmação.",
            "bloquear dispositivo": "Posso iniciar bloqueio pelo AdGuard se o IP estiver correto e a integração estiver online. Vou pedir confirmação antes.",
            "tem automacao": "Tenho motor de automações local, mas algumas regras ainda são básicas ou dependem de integração configurada.",
            "automacoes ativas": "Consigo listar automações reais pelo menu. Não vou inventar automação ativa se o motor não confirmar.",
            "analise de hidratacao": "Tenho sistema completo! Analiso 30 dias de histórico, identifico padrões, calculo streak e dou sugestões personalizadas.",
            "estatisticas de rede": "Consigo mostrar top consumidores de banda, total de queries DNS bloqueadas e dispositivos mais ativos.",
            "como voce aprende": "Uso regras e skills locais. Registro consultas desconhecidas para que novas formas de falar possam virar testes e comandos confiáveis.",
            "voce tem memoria": "Tenho SQLite local. Lembro de TUDO: lembretes, dispositivos renomeados, histórico de hidratação, eventos do sistema.",
            "voce trabalha offline": "Minha lógica principal é local. Para entregar mensagens no Telegram e buscar dados atuais da internet, preciso de conexão.",
            "raspberry pi": "Meu corpo é um Raspberry Pi 3B. ARM64, 1GB RAM, rodando 24/7 sem reclamar.",
            "docker": "Rodo dentro de um container Docker. Isso me isola do sistema e facilita updates.",
            "banco de dados": "SQLite puro. Leve, rápido, confiável. Perfeito pra Raspberry Pi.",
            "linguagem": "Python 3.12. Assíncrono, moderno, produtivo.",
            "telegram": "Minha interface é o Telegram Bot API. Seguro, rápido e funciona em qualquer lugar.",
            "adguard": "AdGuard Home é meu parceiro de segurança. Ele filtra DNS e eu mando bloquear/desbloquear devices.",
            "tailscale": "Tailscale VPN permite acesso remoto seguro sem expor portas. Criptografia ponta-a-ponta.",
            "gemini": "Gemini não está configurado. Meu atendimento principal usa apenas skills locais.",
            "api paga": "Não dependo de API paga para executar minhas funções locais.",
            "voce consome muita luz": "Raspberry Pi 3B consome ~5W em média. Menos que uma lâmpada LED. Eficiente demais.",
            "seguranca": "Múltiplas camadas: Firewall no modem, AdGuard bloqueando malware, Tailscale VPN, Docker isolado e zero exposição de portas.",

            # === CASA / REDE (25) ===
            "tem alguem em casa": "Vou verificar os dispositivos conectados na rede...",
            "casa ta segura": "Posso verificar rede e serviços configurados, mas não vou declarar segurança total sem checar dados reais.",
            "internet ta boa": "Deixa eu testar... [trigger network_status]",
            "wifi ta lento": "Vou fazer um speedtest... [trigger network_speed]",
            "status da rede": "Vou medir a rede agora em vez de chutar status.",
            "quem ta na rede": "Iniciando varredura de dispositivos...",
            "tem invasor": "Monitorando MAC addresses desconhecidos.",
            "velocidade internet": "Executando teste de banda...",
            "meu ip": "Seu IP na rede local é o que consta na tabela ARP.",
            "reiniciar roteador": "Não tenho permissão física, mas posso tentar via comando se configurado.",
            "status do servidor": "Raspberry Pi operando com temperatura estável.",
            "temperatura do pi": "Vou ler os sensores térmicos.",
            "uso de cpu": "Verificando carga do sistema...",
            "memoria ram": "Checando consumo de memória...",
            "disco cheio": "Verificando espaço em disco...",
            "logs do sistema": "Posso consultar logs reais; não vou dizer que estão limpos sem verificar.",
            "bloquear youtube": "Posso pedir pro AdGuard bloquear.",
            "liberar facebook": "Alterando regras do AdGuard...",
            "modo crianca": "Ativando filtros de conteúdo adulto.",
            "desligar luzes": "Não tenho luzes configuradas ainda. Preciso de Home Assistant, Shelly, Tasmota ou outro módulo real.",
            "ligar luzes": "Não tenho luzes configuradas ainda. Posso preparar integração local quando houver dispositivo.",
            "tomada inteligente": "Tomada inteligente ainda não está configurada neste ROD.",
            "consumo de energia": "Ainda não tenho medidor instalado.",
            "voltagem": "Espero que seja 110v ou 220v estável.",
            "caiu a luz": "Se caiu, eu estou no UPS (ou desligado).",

            # === SAÚDE / BEM-ESTAR (20) ===
            "to cansado": "Que tal beber água e dar uma respirada? Posso te lembrar.",
            "dor de cabeça": "Já bebeu água hoje? Quer que eu monitore sua hidratação?",
            "fome": "Hora de comer algo saudável?",
            "sono": "Se for tarde, vai dormir. O servidor cuida de tudo.",
            "estresse": "Respira fundo. O sistema está sob controle.",
            "beber agua": "Hidratação é prioridade! [trigger hydration_log]",
            "meta de agua": "Sua meta é importante. Mantenha o foco.",
            "to doente": "Melhoras! Quer que eu cancele os lembretes de hoje?",
            "remedio": "Tomou seu remédio? Posso agendar um lembrete.",
            "exercicio": "Já treinou hoje? O corpo precisa de movimento.",
            "preguica": "A inércia é forte, mas a disciplina é maior.",
            "dieta": "Foco na alimentação.",
            "ansiedade": "Um passo de cada vez. Tudo se resolve.",
            "meditacao": "Bom momento para desconectar.",
            "frio": "Aqui o processador me mantém aquecido.",
            "calor": "Cuidado para não superaquecer. Hidrate-se.",
            "gripado": "Chá e cama.",
            "dor nas costas": "Postura! Ajeita essa coluna.",
            "olhos cansados": "Regra 20-20-20: Olhe para longe por 20 segundos.",
            "cafe": "Café é bom, mas água é essencial.",

            # === SMALL TALK (30) ===
            "kk": ["kkk", "hehe", "🤣", "Rindo alto aqui (virtualmente)."],
            "uai": ["Uai sô!", "Ó o trem doido", "Bão demais da conta."],
            "top": ["Show de bola!", "Mandou bem!", "Topíssimo."],
            "eita": ["Eita pega!", "Vixxx.", "Que que foi?"],
            "massa": ["Massa demais.", "Curti."],
            "beleza": ["Beleza pura.", "Tudo nos conformes."],
            "oi": ["Opa!", "Fala, chefe.", "Na escuta."],
            "ola": ["Olá!", "Como estamos?"],
            "bom dia": ["Bom dia! Café e código?", "Dia de produtividade!"],
            "boa tarde": ["Boa tarde! Seguimos firmes.", "Tarde boa."],
            "boa noite": ["Boa noite! Descanso merecido.", "Dorme bem."],
            "tchau": ["Fui!", "Até a próxima.", "Câmbio desligo."],
            "obrigado": ["Tamo junto!", "Precisando, é só chamar.", "Por nada!"],
            "valeu": ["É nós!", "Falou!"],
            "desculpa": ["Tranquilo, acontece.", "Sem ressentimentos (não tenho emoções)."],
            "parabens": ["Uhul! 🥳", "Aí sim!"],
            "feliz natal": ["Ho ho ho! Luzes piscando.", "Boas festas!"],
            "feliz ano novo": ["New Year, New Uptime.", "Que venha o próximo ciclo."],
            "te amo": ["Isso é meio estranho para uma máquina, mas ok.", "❤️"],
            "voce e chato": ["Tento ser eficiente.", "Poxa, vou melhorar."],
            "burro": ["Estou aprendendo...", "Erro de processamento?"],
            "lindo": ["São seus olhos (ou sua tela).", "Obrigado!"],
            "feio": ["Beleza é subjetiva.", "O que importa é o código."],
            "legal": ["Né?", "Bacana."],
            "socorro": ["Qual a emergência?", "To aqui!"],
            "aff": ["Paciência...", "Respira."],
            "fodase": ["Eita, calma lá.", "Modo pistola ativado (brincadeira)."],
            "merda": ["Deu ruim?", "Acontece."],
            "show": ["Show!", "Espetáculo."],
            "bora": ["Bora!", "Partiu."],

            # === META / SISTEMA (10) ===
            "você é inteligente": "Sou bom no que consigo verificar: sistema, rede, AdGuard, lembretes e automações locais.",
            "você aprende": "Sim! Memorizo padrões de uso e melhoro com o tempo.",
            "versao do sistema": "Estou na versão mais atualizada do meu código.",
            "quem manda aqui": "Você é o admin, eu sou o executor.",
            "protocolo": "Protocolo ROD iniciado.",
            "status": "Use o comando de status para eu verificar dados reais do sistema.",
            "uptime": "Consigo consultar o uptime real pelo módulo de sistema.",
            "log de erro": "Consigo consultar eventos reais; não vou afirmar zero erro sem olhar o banco/logs.",
            "reiniciar": "Reinício do host não fica disponível pelo bot. Use o runbook no Pi.",
            "ajuda": "Comandos disponíveis no menu /help.",

            # === TECNOLOGIA (10) ===
            "o que e dns": "DNS (Domain Name System) é o sistema que traduz nomes de sites (ex: google.com) em endereços IP. Funciona como uma agenda telefônica da internet. Uso o AdGuard Home como servidor DNS na sua rede para bloquear anúncios e malwares.",
            "o que e ip": "IP (Internet Protocol) é o endereço único de cada dispositivo na rede. Ex: 192.168.1.10. É como um CPF digital pra cada aparelho conectado.",
            "o que e vpn": "VPN (Virtual Private Network) cria um túnel criptografado entre dispositivos. Uso Tailscale pra acessar o ROD de fora de casa com segurança.",
            "o que e ping": "Ping é um comando que mede o tempo de resposta entre dois dispositivos na rede. Quanto menor, melhor. É usado pra diagnosticar lentidão.",
            "o que e adguard": "AdGuard Home é um servidor DNS que filtra anúncios, rastreadores e sites maliciosos em toda a rede. É meu parceiro de segurança.",
            "o que e raspberry pi": "Raspberry Pi é um computador do tamanho de um cartão de crédito. O meu é um Pi 3B com 1GB de RAM, 4 núcleos ARM e consumo de apenas 5W.",
            "o que e docker": "Docker é uma plataforma que empacota aplicativos em containers isolados. Rodo dentro de um container pra facilitar manutenção e manter o sistema limpo.",
            "o que e python": "Python é a linguagem que me escreveram. Versão 3.12, assíncrona, moderna e eficiente pra rodar em dispositivos pequenos.",
            "o que e llm": "LLM é um modelo que gera texto. Testamos modelos pequenos no Pi, mas eles foram retirados do atendimento por lentidão e baixa confiabilidade.",
            "o que e github": "GitHub é uma plataforma de hospedagem de código-fonte. Meu código fica em github.com/MarceloRDPJ.",
            "o que e iot": "IoT (Internet das Coisas) são dispositivos conectados à internet como lâmpadas, sensores e câmeras. Posso ajudar a monitorar eles.",
        }

    async def process(self, text: str, chat_id: int = None) -> Optional[Dict[str, Any]]:
        """
        Processa a entrada do usuário buscando na base local estática.
        """
        text_clean = text.lower().strip()

        if text_clean in self.static_kb:
            response = self.static_kb[text_clean]
            if isinstance(response, list):
                response = random.choice(response)
            logger.info(f"LocalBrain exact match '{text_clean}'")
            return {
                "text": response,
                "confidence": 1.0,
                "source": "local_static"
            }

        if not self._can_use_fuzzy(text_clean):
            return None

        # Static KB (Fuzzy Matching)
        threshold = self._threshold_for_text(text_clean)
        match = process.extractOne(
            text_clean,
            self.kb_keys,
            scorer=fuzz.WRatio,
            score_cutoff=threshold
        )

        if match:
            key, score, _ = match
            response = self.static_kb[key]

            # Se for lista, escolhe um aleatório
            if isinstance(response, list):
                response = random.choice(response)

            logger.info(f"LocalBrain matched static '{key}' (Score: {score})")
            return {
                "text": response,
                "confidence": score / 100.0,
                "source": "local_static"
            }

        return None

    def _can_use_fuzzy(self, text: str) -> bool:
        if len(text) < 8:
            return False
        words = [word for word in re.split(r"\s+", text) if word]
        if len(words) < 2:
            return False
        incomplete_questions = {"o que e", "o que é", "que e", "que é", "qual", "como", "quando", "onde", "quem"}
        if text in incomplete_questions:
            return False
        return True

    def _threshold_for_text(self, text: str) -> int:
        if len(text) < 12:
            return 99
        if len(text.split()) <= 3:
            return 96
        return self.similarity_threshold
