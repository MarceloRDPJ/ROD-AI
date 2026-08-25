"""Cadeia de canais oficiais para ler a fatura da Equatorial.

POR QUE ISSO EXISTE
-------------------
O portal da Agência Virtual recusa a entrada automática em silêncio: recarrega a
tela de acesso sem desafio e sem mensagem, porque o motor antifraude dele pontua
a sessão em vez de perguntar algo. Não existe conserto no cadastro do dono e não
existe tentativa que "insista até dar". O que existe é mais de um canal oficial
publicando o mesmo dado, e a decisão de qual usar agora.

Este módulo é só a POLÍTICA dessa decisão: qual canal tentar, em que ordem, o que
fazer quando um falha e por quanto tempo parar de bater na mesma porta. Ele não
abre navegador, não conversa com o Android e não guarda credencial. Quem executa
é o nó Poco, através de um ``runner`` recebido por parâmetro — o que também torna
a cadeia testável sem telefone.

ORDEM
-----
``WEB_SESSION`` → ``PUBLIC_PAYMENT`` → ``CLARA_WHATSAPP`` → ``OFFICIAL_APP`` →
``CACHE``.

A sessão web vem primeiro porque, quando está viva, é o caminho mais rápido e o
único já provado em produção. O ``CACHE`` vem por último e é INFORMATIVO: ele
descreve a última leitura confirmada, com idade explícita, e nunca autoriza
entregar Pix ou boleto antigos. Cache apresentado como fatura de agora é erro
financeiro, não detalhe de UX.

LIGAR, DESLIGAR OU REORDENAR UM CANAL
-------------------------------------
É configuração, não reescrita:

* ligar → acrescente um ``ProviderSpec`` em ``DEFAULT_CHAIN`` (ou passe
  ``specs=`` ao construtor) com o nome da ação que o agente Android expõe.
  Enquanto essa ação não estiver na fila do Poco, o canal é reportado como
  INDISPONÍVEL e a cadeia segue — sem exceção, sem job enfileirado e sem gastar
  o timeout inteiro para descobrir o óbvio;
* desligar sem esconder → preencha ``missing_requirement``. O canal continua
  declarado (a ordem pretendida fica documentada) e é pulado sempre, porque
  requisito ausente não melhora com o tempo;
* reordenar → mude a ordem da tupla. Se o canal público entregar valor e
  referência sem login, promovê-lo à frente do ``WEB_SESSION`` é mover uma
  linha.

PRIVACIDADE
-----------
O estado de saúde guarda horário, código tipado e fim de cooldown. Nunca
credencial, unidade consumidora, documento, valor, referência, payload Pix ou
código de barras. ``describe()`` foi escrito para poder ir para o log inteiro.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("services.equatorial_providers")

# ---------------------------------------------------------------------------
# NOMES DOS CANAIS
# ---------------------------------------------------------------------------
WEB_SESSION = "web_session"
PUBLIC_PAYMENT = "public_payment"
CLARA_WHATSAPP = "clara_whatsapp"
OFFICIAL_APP = "official_app"
CACHE = "cache"

# ---------------------------------------------------------------------------
# CLASSES DE FALHA
# ---------------------------------------------------------------------------
# node        → o nó Poco não está lá. Todo canal passa por ele, então insistir em
#               qualquer outro é gastar minutos para receber a mesma queda.
# requirement → o canal não existe NESTE aparelho (aplicativo não instalado, conta
#               não registrada). Não é falha: é requisito ausente. Retentar depois
#               de um cooldown não muda nada e só faz o dono esperar no Telegram.
#               Este canal passa a ser pulado SEMPRE, até o requisito mudar.
# structural  → o canal existe e recusou o acesso (antifraude, sessão morta,
#               verificação humana, cofre sem dado). Não melhora em 30 s: cooldown.
# definitive  → resposta sobre a CONTA, não sobre o canal (não há fatura aberta, o
#               imóvel não está no login). Outro canal daria a mesma resposta, e o
#               canal usado continua saudável — nada de cooldown, nada de fila.
# transient   → tropeço pontual (portal lento, WebView que não subiu). Cooldown
#               curto só para não repetir na mesma rajada de toques.
FAILURE_NODE = "node"
FAILURE_REQUIREMENT = "requirement"
FAILURE_STRUCTURAL = "structural"
FAILURE_DEFINITIVE = "definitive"
FAILURE_TRANSIENT = "transient"

# 10 minutos. O prazo não é chute: uma recusa antifraude só muda quando um humano
# entra no portal uma vez, e a mensagem de falha pede exatamente isso. Enquanto
# isso os outros canais atendem, então o custo de esperar é zero para o dono e o
# ganho é não queimar minutos de automação numa porta fechada. Curto demais (30 s,
# como acontecia antes) transformava cada toque em ATUALIZAR numa nova recusa.
STRUCTURAL_COOLDOWN_SECONDS = 600
# 2 minutos: suficiente para não repetir o mesmo tropeço na rajada de toques,
# curto o bastante para o canal preferido voltar sozinho logo.
TRANSIENT_COOLDOWN_SECONDS = 120

# Orçamento total da cadeia. Sem ele, quatro canais a 240 s cada dariam 16 minutos
# de espera para uma mensagem que o dono abriu esperando segundos.
CHAIN_BUDGET_SECONDS = 360
# Abaixo disso não vale começar outro canal: só serviria para estourar no meio.
MIN_PROVIDER_BUDGET_SECONDS = 30
# Leitura local no telefone; não disputa o orçamento dos canais ao vivo.
CACHE_TIMEOUT_SECONDS = 30
# Margem sobre o timeout do job. O ``runner`` já respeita o próprio prazo; esta é
# a rede para o caso de ele não respeitar, para que um canal travado não segure a
# cadeia (e a mensagem do Telegram) para sempre.
RUNNER_GRACE_SECONDS = 20

STRUCTURAL_CODES = frozenset(
    {
        "EQUATORIAL_LOGIN_FAILED",
        "EQUATORIAL_LOGIN_REJECTED",
        "EQUATORIAL_AUTH_REQUIRED",
        "EQUATORIAL_CREDENTIALS_MISSING",
        "EQUATORIAL_HUMAN_CHECK",
        "EQUATORIAL_HUMAN_CHECK_ALL_CHANNELS",
    }
)

DEFINITIVE_CODES = frozenset(
    {
        "EQUATORIAL_PROPERTY_NOT_MAPPED",
        "EQUATORIAL_CONTRACT_NOT_FOUND",
        "EQUATORIAL_UC_NAO_ENCONTRADA",
        "EQUATORIAL_BILL_NOT_FOUND",
        "EQUATORIAL_PAYMENT_DATA_NOT_FOUND",
        "EQUATORIAL_PIX_NOT_FOUND",
        "EQUATORIAL_PIX_AMBIGUOUS",
        "EQUATORIAL_PIX_INVALID",
        "EQUATORIAL_BOLETO_NOT_FOUND",
        "EQUATORIAL_BOLETO_TOO_LARGE",
        "EQUATORIAL_BOLETO_NOT_SENT",
    }
)

TRANSIENT_CODES = frozenset(
    {
        "EQUATORIAL_PORTAL_TIMEOUT",
        "EQUATORIAL_WEBVIEW_UNAVAILABLE",
    }
)

# Requisito ausente no aparelho. O agente pode descobrir isso em tempo de execução;
# quando descobre, o canal sai da fila para sempre em vez de virar cooldown, porque
# cooldown promete uma nova tentativa que não tem como dar certo.
REQUIREMENT_CODES = frozenset(
    {
        "EQUATORIAL_CHANNEL_NOT_INSTALLED",
        "EQUATORIAL_CHANNEL_UNSUPPORTED",
        "EQUATORIAL_REQUIREMENT_MISSING",
        "EQUATORIAL_WHATSAPP_NOT_INSTALLED",
        "EQUATORIAL_WHATSAPP_NOT_REGISTERED",
    }
)

REQUIREMENT_MARKERS = ("nao instalado", "não instalado", "not installed", "nao registrado")

# Falhas do nó, não da concessionária. As frases nascem em ``_run_poco_job`` e no
# despacho da fila; casar por trecho sobrevive a mudanças de pontuação.
NODE_MARKERS = (
    "poco está offline",
    "poco esta offline",
    "sem heartbeat",
    "nó poco está desativado",
    "no poco esta desativado",
    "não confirmou o início",
    "nao confirmou o inicio",
)

# Verificação humana sem código tipado (agente antigo ou canal novo).
HUMAN_CHECK_MARKERS = ("captcha", "imperva", "verificacao humana", "verificação humana")

RUNNER_TIMEOUT_MESSAGE = "O Poco não concluiu a tarefa dentro do tempo esperado."


def equatorial_code(error_text: Any) -> str:
    """Código tipado do agente Android, venha ele embrulhado ou não.

    O agente monta ``classe: mensagem`` antes de devolver o erro, então casar pelo
    início da string nunca funcionou. Procurar em qualquer posição sobrevive a
    qualquer embrulho.
    """
    match = re.search(r"\b(EQUATORIAL_[A-Z_]+)", str(error_text or ""))
    return match.group(1) if match else ""


def classify_failure(error_text: Any) -> str:
    """Classe da falha. Só olha texto de erro — nunca resultado de fatura."""
    text = str(error_text or "")
    lowered = text.lower()
    if any(marker in lowered for marker in NODE_MARKERS):
        return FAILURE_NODE
    code = equatorial_code(text)
    if code in REQUIREMENT_CODES:
        return FAILURE_REQUIREMENT
    if code in STRUCTURAL_CODES:
        return FAILURE_STRUCTURAL
    if code in DEFINITIVE_CODES:
        return FAILURE_DEFINITIVE
    if code in TRANSIENT_CODES:
        return FAILURE_TRANSIENT
    if any(marker in lowered for marker in HUMAN_CHECK_MARKERS):
        return FAILURE_STRUCTURAL
    if not code and any(marker in lowered for marker in REQUIREMENT_MARKERS):
        return FAILURE_REQUIREMENT
    # Código desconhecido (canal novo, agente atualizado) é tratado como tropeço:
    # a cadeia tenta o canal seguinte e volta a este depois do cooldown curto.
    return FAILURE_TRANSIENT


def _declares_age(result: Any) -> bool:
    """O próprio resultado se declara guardado?

    ``cache_age_seconds`` e ``from_cache`` fazem parte do vocabulário que o
    agente Android já fala. Um canal que os preencha está dizendo, com as
    palavras dele, que aquilo não é a fatura de agora — e essa afirmação vale
    mais do que a expectativa estática de quem escreveu a cadeia.

    Idade ausente ou zero conta como leitura de agora: exigir prova de frescor
    de todo canal deixaria a sessão web, que é a única provada, sem poder
    entregar Pix — e aí a proteção custaria o produto.
    """
    if not isinstance(result, Mapping):
        return False
    age = result.get("cache_age_seconds")
    if isinstance(age, (int, float)) and not isinstance(age, bool) and age > 0:
        return True
    return bool(result.get("from_cache"))


def cooldown_for(kind: str) -> float:
    if kind == FAILURE_STRUCTURAL:
        return float(STRUCTURAL_COOLDOWN_SECONDS)
    if kind == FAILURE_TRANSIENT:
        return float(TRANSIENT_COOLDOWN_SECONDS)
    # ``definitive`` e ``node`` não são culpa do canal: penalizá-lo faria a cadeia
    # abandonar o caminho mais rápido por um motivo que não tem a ver com ele.
    return 0.0


@dataclass(frozen=True)
class ProviderSpec:
    """Um canal oficial e a ação que o executa na fila do Poco.

    ``action`` é o contrato com o agente Android. Enquanto a ação não estiver
    registrada na fila, o canal é indisponível — não é erro, é canal desligado.
    """

    name: str
    action: str
    timeout_seconds: Optional[int] = None
    #: Canal informativo: descreve leitura passada. Nunca habilita pagamento.
    informational: bool = False
    #: Parâmetros fixos do contrato, somados a ``{"property": ...}``.
    extra_params: Mapping[str, str] = field(default_factory=dict)
    #: Requisito do aparelho que sabidamente não está atendido. Enquanto tiver
    #: conteúdo, o canal fica DECLARADO e sempre pulado — sem tentativa, sem
    #: cooldown e sem prometer retomada. Esvaziar este campo é o que liga o canal.
    #: Rótulo técnico, para log e diagnóstico; nunca vai para a tela do dono.
    missing_requirement: str = ""

    def params_for(self, property_key: str) -> Dict[str, str]:
        params: Dict[str, str] = {"property": str(property_key)}
        params.update(dict(self.extra_params))
        return params


# A cadeia de produção. Só ``web_session`` e ``cache`` estão implementados no
# agente hoje; os outros três ficam declarados de propósito, para que ligá-los
# seja um registro de ação na fila do Poco e nenhuma mudança aqui. Os nomes de
# ação dos canais novos são a proposta de contrato dos Agentes A e B: enquanto
# não coincidirem com a fila, a cadeia os pula em silêncio.
DEFAULT_CHAIN: Tuple[ProviderSpec, ...] = (
    ProviderSpec(WEB_SESSION, "refresh_equatorial_bills"),
    ProviderSpec(
        PUBLIC_PAYMENT,
        "public_payment_equatorial_bills",
        extra_params={"provider": "equatorial"},
    ),
    # WhatsApp oficial no Poco, vinculado como aparelho adicional: não precisa de
    # SIM nem de API não oficial. A ação é somente leitura e conversa apenas com
    # o contato público da Clara de Goiás.
    ProviderSpec(
        CLARA_WHATSAPP,
        "clara_equatorial_bills",
        timeout_seconds=150,
        extra_params={"provider": "equatorial"},
    ),
    ProviderSpec(
        OFFICIAL_APP,
        "official_app_equatorial_bills",
        extra_params={"provider": "equatorial"},
    ),
    ProviderSpec(
        CACHE,
        "read_bill_cache",
        timeout_seconds=CACHE_TIMEOUT_SECONDS,
        informational=True,
        extra_params={"provider": "equatorial"},
    ),
)


@dataclass
class ProviderHealth:
    """Memória de saúde de um canal. Sem credencial, sem dado de fatura."""

    last_success: Optional[float] = None
    last_failure_reason: str = ""
    cooldown_until: float = 0.0
    #: Requisito ausente descoberto em execução. Diferente de cooldown por
    #: construção: não tem prazo, porque não é o tempo que resolve.
    missing_requirement: str = ""

    def cooling(self, now: float) -> bool:
        return now < self.cooldown_until


@dataclass
class ChainOutcome:
    """O que a cadeia conseguiu, e por qual porta.

    ``failure_text`` sai daqui para a tradução humana que já existe no executor.
    Nome de canal e código tipado ficam em ``attempts``, que é material de log —
    nunca de tela.
    """

    provider: str = ""
    result: Optional[dict] = None
    informational: bool = False
    failure_text: str = ""
    failure_kind: str = ""
    attempts: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Leitura ao vivo confirmada — a única que autoriza Pix/boleto."""
        return self.result is not None and not self.informational

    @property
    def has_reading(self) -> bool:
        return self.result is not None

    @property
    def trail(self) -> str:
        return " → ".join(self.attempts) if self.attempts else "nenhuma tentativa"


Runner = Callable[..., Awaitable[Tuple[Optional[dict], Optional[str]]]]


def _poco_registry() -> Optional[frozenset]:
    """Ações que a fila do Poco aceita hoje, se der para saber.

    ``None`` significa "não sei": nesse caso a cadeia tenta e confia na rede de
    ``ValueError`` do próprio ``enqueue``. Chutar "tudo disponível" ou "nada
    disponível" seria pior que admitir a ignorância.
    """
    try:
        from jarvis.services.poco_node import ALLOWED_ACTIONS

        return frozenset(ALLOWED_ACTIONS)
    except Exception:  # pragma: no cover - importação local sempre existe no Pi
        logger.debug("Não consegui ler o registro de ações do Poco", exc_info=True)
        return None


class EquatorialProviderChain:
    """Escolhe o canal, lembra quem falhou e nunca deixa uma falha subir.

    Uma instância por executor: a memória de saúde só vale se sobreviver entre
    consultas. ``clock`` é injetável para que o teste de cooldown não durma.
    """

    def __init__(
        self,
        specs: Sequence[ProviderSpec] = DEFAULT_CHAIN,
        *,
        clock: Callable[[], float] = time.monotonic,
        available_actions: Optional[Callable[[], Optional[frozenset]]] = None,
        budget_seconds: float = CHAIN_BUDGET_SECONDS,
        default_timeout_seconds: int = 240,
    ) -> None:
        self.specs: Tuple[ProviderSpec, ...] = tuple(specs)
        self._clock = clock
        self._available_actions = available_actions or _poco_registry
        self.budget_seconds = float(budget_seconds)
        self.default_timeout_seconds = int(default_timeout_seconds)
        self.health: Dict[str, ProviderHealth] = {
            spec.name: ProviderHealth() for spec in self.specs
        }

    # ---------------- estado ----------------
    def _entry(self, name: str) -> ProviderHealth:
        return self.health.setdefault(name, ProviderHealth())

    def record_success(self, name: str) -> None:
        """Sucesso zera o cooldown: o canal se provou agora, não em teoria.

        É isto que devolve a preferência ao ``web_session`` assim que a sessão do
        Chrome volta a valer — sem intervenção e sem reiniciar o bot.
        """
        entry = self._entry(name)
        entry.last_success = self._clock()
        entry.last_failure_reason = ""
        entry.cooldown_until = 0.0
        # Entregou: o requisito que faltava passou a existir.
        entry.missing_requirement = ""

    def record_failure(self, name: str, error_text: Any) -> str:
        """Registra a falha e devolve a classe dela. Guarda código, não texto.

        O texto cru do erro pode carregar detalhe do portal; o código tipado (ou
        um rótulo genérico) é o suficiente para decidir e para o log.
        """
        kind = classify_failure(error_text)
        entry = self._entry(name)
        entry.last_failure_reason = equatorial_code(error_text) or kind
        if kind == FAILURE_REQUIREMENT:
            # Sem prazo de volta: o canal só retorna quando o requisito mudar no
            # aparelho, e aí a primeira entrega bem-sucedida limpa este campo.
            entry.missing_requirement = entry.last_failure_reason
            entry.cooldown_until = 0.0
            return kind
        cooldown = cooldown_for(kind)
        if cooldown:
            entry.cooldown_until = self._clock() + cooldown
        return kind

    def missing_requirement(self, spec: ProviderSpec) -> str:
        """Requisito ausente conhecido: por configuração ou descoberto em execução."""
        return spec.missing_requirement or self._entry(spec.name).missing_requirement

    def unavailable(self, spec: ProviderSpec) -> bool:
        """Canal desligado: requisito ausente ou ação fora da fila do Poco.

        Nos dois casos o canal é pulado sem tentativa e sem cooldown. Um canal que
        não existe não é um canal que falhou, e tratar os dois igual fazia a cadeia
        gastar o timeout inteiro para descobrir o que a fila já sabia.
        """
        if self.missing_requirement(spec):
            return True
        registry = self._available_actions()
        if registry is None:
            return False
        return spec.action not in registry

    def is_ready(self, spec: ProviderSpec) -> bool:
        return not self.unavailable(spec) and not self._entry(spec.name).cooling(self._clock())

    def cooling_reason(self, name: str) -> str:
        """Motivo lembrado de um canal em cooldown, ou "" se ele está liberado.

        Quem pede um artefato de pagamento consulta isto antes de enfileirar: se o
        canal que produz o Pix acabou de ter o acesso recusado, o job só serviria
        para o dono esperar o timeout inteiro e receber a mesma recusa. Devolver o
        código guardado deixa a tradução humana montar a frase certa na hora.
        """
        entry = self._entry(name)
        if not entry.cooling(self._clock()):
            return ""
        return entry.last_failure_reason

    def refusal_reason(self, name: str) -> str:
        """Cooldown por ACESSO RECUSADO, e só isso.

        Serve a quem vai pedir um artefato de pagamento. Recusa de acesso se repete
        com certeza, então pular a tentativa é economia de espera. Um tropeço
        (portal lento) não se repete com certeza: bloquear o Pix por causa dele
        transformaria lentidão do portal em "não posso", que é pior e é falso.
        """
        reason = self.cooling_reason(name)
        return reason if reason in STRUCTURAL_CODES else ""

    def preferred(self) -> str:
        """Canal que seria usado agora. Serve a diagnóstico, não à tela do dono."""
        for spec in self.specs:
            if self.is_ready(spec):
                return spec.name
        return ""

    def describe(self) -> Dict[str, Dict[str, Any]]:
        """Retrato do estado, seguro para log: sem credencial e sem dado de conta."""
        now = self._clock()
        snapshot: Dict[str, Dict[str, Any]] = {}
        for spec in self.specs:
            entry = self._entry(spec.name)
            snapshot[spec.name] = {
                "action": spec.action,
                "informational": spec.informational,
                "available": not self.unavailable(spec),
                "missing_requirement": self.missing_requirement(spec),
                "cooling_for_seconds": max(0, int(entry.cooldown_until - now)),
                "last_failure_reason": entry.last_failure_reason,
                "had_success": entry.last_success is not None,
            }
        return snapshot

    # ---------------- execução ----------------
    async def _call(self, runner: Runner, spec: ProviderSpec, property_key: str, timeout: int):
        """Uma tentativa. Nunca levanta: a cadeia inteira depende disso."""
        try:
            return await asyncio.wait_for(
                runner(spec.action, timeout, spec.params_for(property_key)),
                timeout=timeout + RUNNER_GRACE_SECONDS,
            )
        except ValueError:
            # ``enqueue`` recusa ação desconhecida. Rede de segurança para quando o
            # registro não pôde ser lido: canal desligado, não falha de canal.
            logger.info("Canal %s pediu uma ação que a fila do Poco não aceita", spec.name)
            return None, None
        except asyncio.TimeoutError:
            logger.info("Canal %s estourou o prazo local", spec.name)
            return None, RUNNER_TIMEOUT_MESSAGE
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Canal %s falhou de forma inesperada", spec.name)
            return None, RUNNER_TIMEOUT_MESSAGE

    async def read(
        self, property_key: str, runner: Runner, *, ignore_cooldown: bool = False
    ) -> ChainOutcome:
        """Percorre a cadeia até alguém entregar a fatura.

        Regras que o desenho não pode perder de vista:

        * falha de nó aborta tudo — todo canal passa pelo mesmo telefone;
        * resposta definitiva sobre a conta encerra os canais ao vivo, porque o
          próximo diria a mesma coisa; o cache informativo ainda entra, rotulado;
        * cada canal é tentado no máximo UMA vez por consulta. É esta garantia,
          e não o cooldown, que impede laço entre canais.

        ``ignore_cooldown`` existe para o ATUALIZAR que o dono aperta com o dedo.
        O cooldown supõe que nada mudou desde a última recusa, e essa suposição é
        exatamente a que um toque em ATUALIZAR desmente: quem aperta costuma ter
        acabado de entrar no portal. Sem esta porta, o dono resolveria o problema
        e continuaria recebendo a recusa lembrada por dez minutos — e concluiria,
        com razão, que o ROD não olha de novo. Consulta automática não usa isto:
        lá o cooldown é justamente o que evita queimar minutos numa porta fechada.
        """
        started = self._clock()
        attempts: list[str] = []
        first_failure = ""
        definitive = ""
        skip_live = False

        for spec in self.specs:
            entry = self._entry(spec.name)
            now = self._clock()

            requirement = self.missing_requirement(spec)
            if requirement:
                attempts.append(f"{spec.name}:sem_requisito:{requirement}")
                continue
            if self.unavailable(spec):
                attempts.append(f"{spec.name}:indisponivel")
                continue
            # O cache é a cauda da cadeia: ele ainda entra depois de uma resposta
            # definitiva ou do fim do orçamento, porque rotular leitura antiga é
            # barato e é melhor que devolver só "não consegui".
            if skip_live and not spec.informational:
                attempts.append(f"{spec.name}:dispensado")
                continue
            if entry.cooling(now) and not ignore_cooldown:
                attempts.append(f"{spec.name}:cooldown")
                continue

            if spec.informational:
                timeout = int(spec.timeout_seconds or CACHE_TIMEOUT_SECONDS)
            else:
                remaining = self.budget_seconds - (now - started)
                if remaining < MIN_PROVIDER_BUDGET_SECONDS:
                    attempts.append(f"{spec.name}:sem_tempo")
                    skip_live = True
                    continue
                timeout = int(min(spec.timeout_seconds or self.default_timeout_seconds, remaining))

            result, error = await self._call(runner, spec, property_key, timeout)

            if error is None and result is None:
                # Ação recusada pela fila: canal desligado, não canal quebrado.
                attempts.append(f"{spec.name}:indisponivel")
                continue

            if error:
                kind = self.record_failure(spec.name, error)
                attempts.append(f"{spec.name}:{kind}:{entry.last_failure_reason or 'sem_codigo'}")
                if kind == FAILURE_NODE:
                    logger.info("Cadeia Equatorial abortada pelo nó | %s", " → ".join(attempts))
                    return ChainOutcome(
                        failure_text=str(error),
                        failure_kind=kind,
                        attempts=tuple(attempts),
                    )
                if kind == FAILURE_DEFINITIVE and not definitive:
                    definitive = str(error)
                    skip_live = True
                # Requisito ausente não é motivo que o dono possa acionar, e o texto
                # do agente pode nomear o canal. Fica no log e morre aqui.
                if not first_failure and kind != FAILURE_REQUIREMENT:
                    first_failure = str(error)
                continue

            self.record_success(spec.name)
            # O RESULTADO tem a última palavra sobre ser leitura de agora.
            # Declarar isso no ``ProviderSpec`` bastava enquanto só o canal de
            # cache devolvia dado guardado; qualquer canal ao vivo que devolva a
            # última leitura que ele mesmo tinha era apresentado como consulta de
            # agora — com hora exata na tela e PIX/BOLETO liberados sobre um dado
            # que o próprio payload declarava velho. O canal não é quem sabe: o
            # payload é. Quem diz a idade dela é obedecido.
            stale = _declares_age(result)
            informational = spec.informational or stale
            if stale and not spec.informational:
                logger.info(
                    "Canal %s devolveu leitura guardada; rebaixada a informativa",
                    spec.name,
                )
            attempts.append(f"{spec.name}:ok" if not stale else f"{spec.name}:ok_guardado")
            logger.info(
                "Fatura da Equatorial obtida | canal=%s informativo=%s | %s",
                spec.name,
                informational,
                " → ".join(attempts),
            )
            # Falha do canal preferido continua sendo a orientação útil quando o
            # que chegou é só cache. Sem isso o dono veria a leitura antiga sem
            # saber por que a consulta de agora não veio.
            reason = ""
            if informational:
                # Uma recusa estrutural ainda em cooldown é mais útil que um
                # tropeço transitório de um canal secundário tentado agora.
                # Sem essa prioridade, o cache dizia apenas "ação não
                # configurada" e escondia que a sessão oficial exigia login.
                reason = definitive or self._remembered_reason() or first_failure
            return ChainOutcome(
                provider=spec.name,
                result=result or {},
                informational=informational,
                failure_text=reason,
                failure_kind=classify_failure(reason) if reason else "",
                attempts=tuple(attempts),
            )

        # Nada entregou. A falha mais útil é a resposta definitiva sobre a conta;
        # depois a primeira falha real; e, se tudo foi pulado por cooldown, o
        # motivo lembrado — que reconstrói a orientação sem repetir a tentativa.
        failure_text = definitive or first_failure or self._remembered_reason()
        outcome = ChainOutcome(
            failure_text=failure_text,
            failure_kind=classify_failure(failure_text) if failure_text else "",
            attempts=tuple(attempts),
        )
        logger.info("Nenhum canal da Equatorial entregou | %s", outcome.trail)
        return outcome

    def _remembered_reason(self) -> str:
        """Último motivo conhecido de um canal em cooldown.

        Devolver o código guardado deixa a tradução humana já existente montar a
        frase certa mesmo quando nenhuma tentativa foi feita nesta consulta.
        """
        now = self._clock()
        for spec in self.specs:
            entry = self._entry(spec.name)
            if entry.cooling(now) and entry.last_failure_reason.startswith("EQUATORIAL_"):
                return entry.last_failure_reason
        return ""
