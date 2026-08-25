"""Cadeia de canais oficiais da Equatorial: escolha, cooldown e cache honesto.

O portal recusa a entrada automática em silêncio, então a consulta deixou de ser
"um caminho que às vezes falha" e passou a ser "vários canais oficiais e uma
decisão". Estes testes cobrem a decisão, não o telefone: o ``runner`` é dublê e a
fila do Poco é um conjunto de nomes de ação.

O que está sob prova aqui, em ordem de gravidade:

1. cache NUNCA vira Pix ou boleto — leitura antiga apresentada como cobrança de
   agora é o erro caro desta tela, porque quem paga não tem como perceber;
2. canal sem requisito é pulado, enquanto a Clara vinculada participa da cadeia;
3. recusa estrutural não é repetida 30 s depois, e o canal preferido volta sozinho
   quando o prazo passa;
4. nenhum nome de canal interno chega à tela do Telegram.

Nenhum dado real aparece: valor, referência, unidade consumidora e payload Pix são
fixtures inventadas.
"""

import asyncio
import sys
import types

import pytest

from jarvis.config import Config
from jarvis.core import router
from jarvis.core.executor import Executor
from jarvis.services.equatorial_providers import (
    CACHE,
    CLARA_WHATSAPP,
    DEFAULT_CHAIN,
    OFFICIAL_APP,
    PUBLIC_PAYMENT,
    STRUCTURAL_COOLDOWN_SECONDS,
    TRANSIENT_COOLDOWN_SECONDS,
    WEB_SESSION,
    EquatorialProviderChain,
)

FAKE_PIX = "PIXFAKEPAYLOAD-0000-TESTE"
# Unidade consumidora inventada, só para provar que ela não vaza para o log.
FAKE_UC = "UC-99999999"

ACTIONS = {spec.name: spec.action for spec in DEFAULT_CHAIN}
EVERYTHING_WIRED = frozenset(spec.action for spec in DEFAULT_CHAIN)
# A fila real de hoje: sessão web, Clara oficial e cache.
WIRED_TODAY = frozenset({ACTIONS[WEB_SESSION], ACTIONS[CLARA_WHATSAPP], ACTIONS[CACHE]})


def wire(code, detail="detalhe tecnico"):
    """Formato exato em que o agente Android entrega um erro tipado."""
    return f"IllegalStateException: {code}: {detail}"


class FakeClock:
    """Relógio controlado: cooldown de 10 minutos não se testa dormindo."""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class Runner:
    """Dublê da fila do Poco. Registra tudo o que foi realmente tentado."""

    def __init__(self, outcomes=None, *, clock=None, spend=None, raises=None):
        self.outcomes = dict(outcomes or {})
        self.clock = clock
        self.spend = dict(spend or {})
        self.raises = dict(raises or {})
        self.calls = []

    async def __call__(self, action, timeout_seconds=70, params=None):
        self.calls.append({"action": action, "timeout": timeout_seconds, "params": dict(params or {})})
        if self.clock is not None and action in self.spend:
            self.clock.advance(self.spend[action])
        if action in self.raises:
            raise self.raises[action]
        return self.outcomes.get(action, (None, "acao nao configurada no teste"))

    def actions(self):
        return [call["action"] for call in self.calls]

    def tried(self, provider):
        return ACTIONS[provider] in self.actions()


def build_chain(*, clock=None, registry=EVERYTHING_WIRED, specs=DEFAULT_CHAIN, budget=360.0):
    return EquatorialProviderChain(
        specs,
        clock=clock or FakeClock(),
        available_actions=lambda: registry,
        budget_seconds=budget,
        default_timeout_seconds=240,
    )


def bill_result(**extra):
    data = {"amount": "R$ 187,90", "reference": "07/2026", "due_date": "12/08/2026"}
    data.update(extra)
    return data


def cache_result(**extra):
    data = {"amount": "R$ 210,44", "due_date": "10/08/2026", "cache_age_seconds": 7200}
    data.update(extra)
    return data


# =====================================================
# ESCOLHA DE CANAL
# =====================================================
@pytest.mark.asyncio
async def test_live_web_session_is_used_alone_and_nothing_else_is_tried():
    """Sessão viva é o caminho mais rápido: os outros canais nem são tocados."""
    chain = build_chain()
    runner = Runner({ACTIONS[WEB_SESSION]: (bill_result(), None)})

    outcome = await chain.read("casa", runner)

    assert outcome.provider == WEB_SESSION
    assert outcome.ok
    assert not outcome.informational
    assert runner.actions() == [ACTIONS[WEB_SESSION]]


@pytest.mark.asyncio
async def test_refused_web_login_falls_to_the_next_channel_in_the_same_query():
    """A recusa antifraude não pode virar "não consegui": há outro canal oficial."""
    chain = build_chain()
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None),
        }
    )

    outcome = await chain.read("casa", runner)

    assert outcome.provider == PUBLIC_PAYMENT
    assert outcome.ok
    assert runner.tried(WEB_SESSION) and runner.tried(PUBLIC_PAYMENT)


@pytest.mark.asyncio
async def test_a_channel_is_attempted_at_most_once_per_query():
    """Garantia contra laço: a cadeia anda para frente, nunca em círculo."""
    chain = build_chain()
    runner = Runner(
        {action: (None, wire("EQUATORIAL_PORTAL_TIMEOUT")) for action in EVERYTHING_WIRED}
    )

    await chain.read("casa", runner)

    assert sorted(runner.actions()) == sorted(set(runner.actions()))
    assert runner.tried(CLARA_WHATSAPP)


# =====================================================
# COOLDOWN
# =====================================================
@pytest.mark.asyncio
async def test_a_refused_login_is_not_retried_thirty_seconds_later():
    """O sintoma que originou a cadeia: repetir o login web a cada toque."""
    clock = FakeClock()
    chain = build_chain(clock=clock)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None),
        }
    )

    await chain.read("casa", runner)
    clock.advance(30)
    second = Runner({ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None)})
    outcome = await chain.read("casa", second)

    assert not second.tried(WEB_SESSION)
    assert outcome.provider == PUBLIC_PAYMENT


@pytest.mark.asyncio
async def test_a_live_channel_returning_a_stored_reading_is_demoted_to_informational():
    """O RESULTADO decide se é leitura de agora, não a expectativa do canal.

    Declarar isso no ``ProviderSpec`` bastava enquanto só o canal de cache
    devolvia dado guardado. Um canal ao vivo que devolva a última leitura que ele
    mesmo tinha era apresentado como consulta de agora — com hora exata na tela e
    PIX/BOLETO liberados sobre dado que o próprio payload declarava velho.
    """
    chain = build_chain()
    guardado = bill_result(cache_age_seconds=86400)
    outcome = await chain.read("casa", Runner({ACTIONS[WEB_SESSION]: (guardado, None)}))

    assert outcome.has_reading
    assert outcome.informational, "payload declarou idade; não é leitura de agora"
    assert not outcome.ok, "leitura guardada não pode liberar PIX/BOLETO"


@pytest.mark.asyncio
async def test_from_cache_alone_is_enough_to_demote_a_live_channel():
    """``from_cache`` sem idade também é o canal dizendo que não é de agora."""
    chain = build_chain()
    guardado = bill_result(from_cache=True)
    outcome = await chain.read("casa", Runner({ACTIONS[WEB_SESSION]: (guardado, None)}))

    assert not outcome.ok


@pytest.mark.asyncio
async def test_a_reading_without_declared_age_still_counts_as_live():
    """Sem esta contraparte, a proteção custaria o produto.

    Exigir prova de frescor de todo canal deixaria a sessão web — a única
    provada, e que não carimba idade — sem poder entregar Pix nunca.
    """
    chain = build_chain()
    outcome = await chain.read("casa", Runner({ACTIONS[WEB_SESSION]: (bill_result(), None)}))

    assert outcome.ok


@pytest.mark.asyncio
async def test_refresh_pressed_during_a_running_query_still_tries_the_channel(monkeypatch):
    """ATUALIZAR apertado no meio de uma consulta não pode herdar o voo antigo.

    Cenário real: o dono manda a consulta, ela demora, ele se impacienta e aperta
    ATUALIZAR. Antes, o segundo toque aderia ao voo em curso — que respeitou o
    cooldown — e o canal preferido NUNCA era tentado. Ele recebia a recusa
    lembrada com a impressão, correta, de que o botão não fez nada.
    """
    clock = FakeClock()
    executor = build_executor(
        monkeypatch,
        jobs={ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED"))},
        clock=clock,
        delays={ACTIONS[WEB_SESSION]: 0.05},
    )
    # Primeira recusa: o canal entra em cooldown.
    await executor._equatorial_bill_flow(1, "casa")
    executor.poco_calls.clear()

    # Consulta comum em voo, e o dono aperta ATUALIZAR enquanto ela corre.
    comum = asyncio.ensure_future(executor._equatorial_bill_flow(1, "casa"))
    await asyncio.sleep(0)
    await executor.handle_bill_callback(1, "bill_refresh:equatorial:casa", FakeQuery(99))
    await comum

    assert job_calls(executor, ACTIONS[WEB_SESSION]), (
        "o ATUALIZAR do dono aderiu ao voo não forçado e não tentou o canal"
    )


@pytest.mark.asyncio
async def test_the_owner_pressing_refresh_gets_a_fresh_attempt_despite_the_cooldown():
    """ATUALIZAR do dono desmente a suposição do cooldown.

    O cooldown existe porque nada mudou desde a recusa. Quem aperta ATUALIZAR
    normalmente acabou de entrar no portal, e sem esta porta resolveria o
    problema e continuaria recebendo a recusa lembrada por dez minutos.
    """
    clock = FakeClock()
    chain = build_chain(clock=clock)
    recusa = Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED"))})
    await chain.read("casa", recusa)

    # Um segundo depois: o dono entrou no portal e apertou ATUALIZAR.
    clock.advance(1)
    depois = Runner({ACTIONS[WEB_SESSION]: (bill_result(), None)})
    outcome = await chain.read("casa", depois, ignore_cooldown=True)

    assert depois.tried(WEB_SESSION), "ATUALIZAR explicito tem de tentar de novo"
    assert outcome.ok and outcome.provider == WEB_SESSION


@pytest.mark.asyncio
async def test_an_automatic_query_still_respects_the_cooldown():
    """A dispensa é só do dedo do dono; consulta automática não a herda."""
    clock = FakeClock()
    chain = build_chain(clock=clock)
    await chain.read("casa", Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED"))}))

    clock.advance(1)
    automatica = Runner({ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None)})
    await chain.read("casa", automatica)

    assert not automatica.tried(WEB_SESSION)


@pytest.mark.asyncio
async def test_web_session_is_preferred_again_as_soon_as_the_cooldown_expires():
    """Q21: a sessão volta a valer sem intervenção e sem reiniciar o bot."""
    clock = FakeClock()
    chain = build_chain(clock=clock)
    await chain.read(
        "casa", Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED"))})
    )
    assert chain.preferred() != WEB_SESSION

    clock.advance(STRUCTURAL_COOLDOWN_SECONDS + 1)

    assert chain.preferred() == WEB_SESSION
    runner = Runner({ACTIONS[WEB_SESSION]: (bill_result(), None)})
    outcome = await chain.read("casa", runner)
    assert outcome.provider == WEB_SESSION
    assert runner.actions() == [ACTIONS[WEB_SESSION]]


@pytest.mark.asyncio
async def test_a_stumble_costs_less_than_a_refusal():
    """Portal lento volta em minutos; recusa de acesso só volta com humano."""
    clock = FakeClock()
    chain = build_chain(clock=clock)

    await chain.read(
        "casa", Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_PORTAL_TIMEOUT"))})
    )
    assert chain.preferred() != WEB_SESSION

    clock.advance(TRANSIENT_COOLDOWN_SECONDS + 1)
    assert chain.preferred() == WEB_SESSION
    assert TRANSIENT_COOLDOWN_SECONDS < STRUCTURAL_COOLDOWN_SECONDS


@pytest.mark.asyncio
async def test_a_successful_reading_clears_a_pending_cooldown():
    clock = FakeClock()
    chain = build_chain(clock=clock)
    await chain.read(
        "casa", Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED"))})
    )

    chain.record_success(WEB_SESSION)

    assert chain.preferred() == WEB_SESSION
    assert chain.cooling_reason(WEB_SESSION) == ""


@pytest.mark.asyncio
async def test_an_answer_about_the_account_never_punishes_the_channel():
    """Não ter fatura aberta é fato da conta. Cooldown aqui cegaria o canal bom."""
    chain = build_chain()
    runner = Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_BILL_NOT_FOUND"))})

    await chain.read("casa", runner)

    assert chain.cooling_reason(WEB_SESSION) == ""
    assert chain.preferred() == WEB_SESSION


@pytest.mark.asyncio
async def test_a_definitive_answer_stops_the_live_channels():
    """Se o imóvel não está no login, o canal seguinte diria a mesma coisa."""
    chain = build_chain()
    runner = Runner({ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_CONTRACT_NOT_FOUND"))})

    outcome = await chain.read("casa", runner)

    assert not runner.tried(PUBLIC_PAYMENT)
    assert not runner.tried(OFFICIAL_APP)
    assert runner.tried(CACHE)  # o rótulo de leitura antiga ainda é permitido
    assert "EQUATORIAL_CONTRACT_NOT_FOUND" in outcome.failure_text


@pytest.mark.asyncio
async def test_a_node_failure_aborts_the_whole_chain():
    """Todo canal passa pelo mesmo telefone: insistir só custa minutos ao dono."""
    chain = build_chain()
    runner = Runner({ACTIONS[WEB_SESSION]: (None, "O Poco está offline ou sem heartbeat recente.")})

    outcome = await chain.read("casa", runner)

    assert runner.actions() == [ACTIONS[WEB_SESSION]]
    assert not outcome.has_reading
    assert chain.cooling_reason(WEB_SESSION) == ""  # não é culpa do canal


# =====================================================
# CANAL INDISPONÍVEL / REQUISITO AUSENTE
# =====================================================
@pytest.mark.asyncio
async def test_an_action_absent_from_the_poco_queue_is_skipped_without_a_job():
    """Contrato que ainda não existe é canal desligado, não exceção."""
    chain = build_chain(registry=WIRED_TODAY)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[CLARA_WHATSAPP]: (None, wire("EQUATORIAL_PORTAL_TIMEOUT")),
            ACTIONS[CACHE]: (cache_result(), None),
        }
    )

    outcome = await chain.read("casa", runner)

    assert runner.actions() == [
        ACTIONS[WEB_SESSION], ACTIONS[CLARA_WHATSAPP], ACTIONS[CACHE]
    ]
    assert outcome.provider == CACHE


@pytest.mark.asyncio
async def test_linked_whatsapp_is_the_live_fallback_after_web_failure():
    """A Clara oficial atende quando a sessão web é recusada pelo antifraude."""
    chain = build_chain(registry=WIRED_TODAY)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[CLARA_WHATSAPP]: (bill_result(), None),
        }
    )

    outcome = await chain.read("casa", runner)

    assert runner.tried(CLARA_WHATSAPP)
    assert outcome.provider == CLARA_WHATSAPP
    assert outcome.ok
    snapshot = chain.describe()[CLARA_WHATSAPP]
    assert snapshot["available"] is True
    assert not snapshot["missing_requirement"]


@pytest.mark.asyncio
async def test_a_missing_requirement_found_at_runtime_becomes_a_permanent_skip():
    clock = FakeClock()
    chain = build_chain(clock=clock)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[OFFICIAL_APP]: (None, wire("EQUATORIAL_CHANNEL_NOT_INSTALLED")),
        }
    )
    await chain.read("casa", runner)
    assert runner.tried(OFFICIAL_APP)

    clock.advance(STRUCTURAL_COOLDOWN_SECONDS * 10)
    second = Runner({ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None)})
    await chain.read("casa", second)

    assert not second.tried(OFFICIAL_APP)


@pytest.mark.asyncio
async def test_a_channel_that_starts_working_is_taken_back():
    """O requisito pode mudar no aparelho; a primeira entrega reabilita o canal."""
    chain = build_chain()
    chain.record_failure(OFFICIAL_APP, wire("EQUATORIAL_CHANNEL_NOT_INSTALLED"))
    assert chain.describe()[OFFICIAL_APP]["available"] is False

    chain.record_success(OFFICIAL_APP)

    assert chain.describe()[OFFICIAL_APP]["available"] is True


@pytest.mark.asyncio
async def test_a_queue_refusal_does_not_raise_and_does_not_stop_the_chain():
    """``enqueue`` levanta ValueError para ação desconhecida. Isso não é falha."""
    chain = build_chain()
    runner = Runner(
        {ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None)},
        raises={ACTIONS[WEB_SESSION]: ValueError("Unsupported Poco action")},
    )

    outcome = await chain.read("casa", runner)

    assert outcome.provider == PUBLIC_PAYMENT
    assert chain.cooling_reason(WEB_SESSION) == ""


@pytest.mark.asyncio
async def test_an_unexpected_exception_in_one_channel_does_not_break_the_query():
    chain = build_chain()
    runner = Runner(
        {ACTIONS[PUBLIC_PAYMENT]: (bill_result(), None)},
        raises={ACTIONS[WEB_SESSION]: RuntimeError("estouro inesperado")},
    )

    outcome = await chain.read("casa", runner)

    assert outcome.provider == PUBLIC_PAYMENT
    assert chain.cooling_reason(WEB_SESSION)  # tropeço: cooldown curto


@pytest.mark.asyncio
async def test_a_slow_channel_does_not_spend_the_whole_afternoon():
    """Orçamento total: quatro canais lentos não podem virar 16 minutos de espera."""
    clock = FakeClock()
    chain = build_chain(clock=clock, budget=100.0)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_PORTAL_TIMEOUT")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
        clock=clock,
        spend={ACTIONS[WEB_SESSION]: 90},
    )

    outcome = await chain.read("casa", runner)

    assert not runner.tried(PUBLIC_PAYMENT)
    assert not runner.tried(OFFICIAL_APP)
    assert outcome.provider == CACHE  # o rótulo de leitura antiga é barato


@pytest.mark.asyncio
async def test_the_health_snapshot_carries_no_account_data(caplog):
    """Estado de saúde é código e prazo. Nunca UC, documento ou valor."""
    chain = build_chain()
    runner = Runner(
        {ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_REJECTED", FAKE_UC))}
    )

    with caplog.at_level("DEBUG"):
        await chain.read("casa", runner)

    assert FAKE_UC not in repr(chain.describe())
    assert FAKE_UC not in caplog.text


# =====================================================
# CACHE É INFORMATIVO — NUNCA PAGAMENTO
# =====================================================
@pytest.mark.asyncio
async def test_the_cache_reading_is_never_treated_as_a_live_one():
    chain = build_chain(registry=WIRED_TODAY)
    runner = Runner(
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[CACHE]: (cache_result(), None),
        }
    )

    outcome = await chain.read("casa", runner)

    assert outcome.has_reading
    assert outcome.informational
    assert not outcome.ok  # é ``ok`` que libera Pix e boleto
    assert "EQUATORIAL_LOGIN_FAILED" in outcome.failure_text


@pytest.mark.asyncio
async def test_a_cache_only_answer_keeps_the_actionable_reason_even_during_cooldown():
    """Sem o motivo lembrado, o dono veria leitura antiga sem saber o porquê."""
    clock = FakeClock()
    chain = build_chain(clock=clock, registry=WIRED_TODAY)
    await chain.read(
        "casa",
        Runner(
            {
                ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
                ACTIONS[CACHE]: (cache_result(), None),
            }
        ),
    )

    clock.advance(TRANSIENT_COOLDOWN_SECONDS + 1)  # o cache volta, a sessão não
    outcome = await chain.read("casa", Runner({ACTIONS[CACHE]: (cache_result(), None)}))

    assert outcome.informational
    assert "EQUATORIAL_AUTH_REQUIRED" in outcome.failure_text


# =====================================================
# TELEGRAM — A EXPERIÊNCIA NÃO MUDA
# =====================================================
class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeQuery:
    def __init__(self, message_id=77):
        self.message = FakeMessage(message_id)


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.documents = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return FakeMessage(500 + len(self.sent))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append({"message_id": message_id, "text": text, "reply_markup": reply_markup})
        return FakeMessage(message_id)

    async def send_document(self, chat_id, document, filename=None, caption=None):
        self.documents.append({"filename": filename, "caption": caption})
        return FakeMessage(900)


class StubButton:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class StubMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


def install_telegram_stub(monkeypatch):
    """O ``python-telegram-bot`` local está corrompido (``import telegram`` falha).

    No Pi a biblioteca está íntegra na 20.8. Sem este stub mínimo o executor cai —
    de propósito — para "sem teclado", e o cabeamento dos botões não seria provado.
    """
    module = types.ModuleType("telegram")
    module.InlineKeyboardButton = StubButton
    module.InlineKeyboardMarkup = StubMarkup
    monkeypatch.setitem(sys.modules, "telegram", module)


def build_executor(monkeypatch, jobs=None, *, registry=None, clock=None, delays=None):
    application = type("App", (), {"bot": FakeBot()})()
    executor = Executor(application)
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 1, raising=False)
    calls = []

    async def fake_run_poco_job(action, timeout_seconds=70, params=None):
        calls.append({"action": action, "params": dict(params or {})})
        if delays and action in delays:
            await asyncio.sleep(delays[action])
        return (jobs or {}).get(action, (None, "acao nao configurada no teste"))

    monkeypatch.setattr(executor, "_run_poco_job", fake_run_poco_job)
    if registry is not None or clock is not None:
        executor._equatorial_providers = build_chain(
            clock=clock, registry=registry if registry is not None else WIRED_TODAY
        )
    executor.poco_calls = calls
    return executor


def job_calls(executor, action):
    return [call for call in executor.poco_calls if call["action"] == action]


def callback_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


CHANNEL_LEAKS = (
    "clara",
    "whatsapp",
    "web_session",
    "public_payment",
    "official_app",
    "cooldown",
    "drs",
    "canal público",
    "provider",
)


def assert_no_channel_leak(executor):
    """A tela do dono não fala de canal, motor nem estado interno da cadeia."""
    texts = [item["text"] for item in executor.app.bot.sent + executor.app.bot.edited]
    for text in texts:
        lowered = text.lower()
        for leak in CHANNEL_LEAKS:
            assert leak not in lowered, f"vazou {leak!r} para o Telegram"


@pytest.mark.asyncio
async def test_the_query_still_opens_and_ends_in_a_single_message(monkeypatch):
    """Roteador → executor → job com a cadeia no meio: UMA mensagem editada."""
    intent = await router.route("conta de luz casa", 1)
    executor = build_executor(monkeypatch, {ACTIONS[WEB_SESSION]: ({"amount": "R$ 187,90", "reference": "07/2026"}, None)})

    response = await executor.execute(intent, 1)

    assert response is None
    assert [item["text"] for item in executor.app.bot.sent] == [
        "⚡ Consultando Equatorial — Casa..."
    ]
    assert len(executor.app.bot.edited) == 1
    assert "R$ 187,90" in executor.app.bot.edited[-1]["text"]
    assert_no_channel_leak(executor)


@pytest.mark.asyncio
async def test_changing_channel_is_invisible_to_the_owner(monkeypatch):
    """A sessão web caiu e outro canal oficial entregou. O dono só vê a conta."""
    install_telegram_stub(monkeypatch)
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[PUBLIC_PAYMENT]: ({"amount": "R$ 187,90", "reference": "07/2026"}, None),
        },
        registry=EVERYTHING_WIRED,
    )

    await executor._equatorial_bill_flow(1, "casa")

    final = executor.app.bot.edited[-1]
    assert "R$ 187,90" in final["text"]
    assert "antifraude" not in final["text"].lower()  # falha do canal não é notícia
    assert callback_data(final["reply_markup"]) == [
        "bill_pix:equatorial:casa",
        "bill_boleto:equatorial:casa",
        "bill_refresh:equatorial:casa",
        "menu_contas",
    ]
    assert_no_channel_leak(executor)


@pytest.mark.asyncio
async def test_a_cache_only_answer_is_labelled_and_offers_no_payment(monkeypatch):
    """Leitura antiga com data explícita e SEM PIX nem BOLETO na tela."""
    install_telegram_stub(monkeypatch)
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
    )

    await executor._equatorial_bill_flow(1, "casa")

    final = executor.app.bot.edited[-1]
    assert "Última leitura confirmada" in final["text"]
    assert "cache do Poco" in final["text"]
    assert "R$ 210,44" in final["text"]
    assert callback_data(final["reply_markup"]) == [
        "bill_refresh:equatorial:casa",
        "menu_contas",
    ]
    assert_no_channel_leak(executor)


@pytest.mark.asyncio
async def test_the_cache_never_shows_an_old_payment_code(monkeypatch):
    """Cache com Pix guardado: o valor pode aparecer rotulado, o código nunca."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[CACHE]: (cache_result(pix=FAKE_PIX, barcode="82640000001-8"), None),
        },
    )

    await executor._equatorial_bill_flow(1, "casa")

    final = executor.app.bot.edited[-1]["text"]
    assert "R$ 210,44" in final
    assert FAKE_PIX not in final
    assert "82640000001-8" not in final


@pytest.mark.asyncio
async def test_pressing_pix_after_a_cache_only_answer_is_refused(monkeypatch):
    """Botão de mensagem antiga não pode virar código de pagamento antigo."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
    )
    await executor._equatorial_bill_flow(1, "casa")

    await executor.handle_bill_callback(1, "bill_pix:equatorial:casa", FakeQuery())

    assert job_calls(executor, "get_equatorial_pix") == []
    last = executor.app.bot.sent[-1]["text"]
    assert "leitura guardada" in last
    assert "Nenhum pagamento foi realizado" in last


@pytest.mark.asyncio
async def test_a_cache_only_answer_discards_the_artifact_kept_in_memory(monkeypatch):
    """O Pix da leitura ao vivo anterior morre quando só sobra cache."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
    )
    executor._bill_artifacts[("equatorial", "casa", "pix")] = {
        "payload": FAKE_PIX,
        "reference": "07/2026",
        "captured_at": 10 ** 12,
    }

    await executor._equatorial_bill_flow(1, "casa")
    await executor._send_bill_pix(1, "casa")

    assert executor._bill_artifacts == {}
    assert all(FAKE_PIX not in item["text"] for item in executor.app.bot.sent)


@pytest.mark.asyncio
async def test_pressing_boleto_after_a_cache_only_answer_is_refused(monkeypatch):
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
    )
    await executor._equatorial_bill_flow(1, "casa")
    # Sem o cooldown no caminho, a única coisa que pode recusar aqui é a trava de
    # leitura antiga. É ela que este teste precisa provar.
    executor._equatorial_providers.record_success(WEB_SESSION)

    await executor.handle_bill_callback(1, "bill_boleto:equatorial:casa", FakeQuery())

    assert job_calls(executor, "get_equatorial_boleto") == []
    assert executor.app.bot.documents == []
    assert "leitura guardada" in executor.app.bot.sent[-1]["text"]


@pytest.mark.asyncio
async def test_a_live_reading_releases_the_payment_buttons_again(monkeypatch):
    """A trava é do estado, não do imóvel: leitura ao vivo destrava na hora."""
    clock = FakeClock()
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_AUTH_REQUIRED")),
            ACTIONS[CACHE]: (cache_result(), None),
        },
        clock=clock,
    )
    await executor._equatorial_bill_flow(1, "casa")
    assert executor._only_stale_reading("equatorial", "casa")

    clock.advance(STRUCTURAL_COOLDOWN_SECONDS + 1)
    executor.poco_jobs_live = True

    async def live_job(action, timeout_seconds=70, params=None):
        executor.poco_calls.append({"action": action, "params": dict(params or {})})
        if action == "get_equatorial_pix":
            return {"reference": "07/2026", "pix_payload": FAKE_PIX}, None
        return {"amount": "R$ 187,90", "reference": "07/2026"}, None

    monkeypatch.setattr(executor, "_run_poco_job", live_job)
    await executor._equatorial_bill_flow(1, "casa")

    assert not executor._only_stale_reading("equatorial", "casa")
    await executor._send_bill_pix(1, "casa")
    assert any(FAKE_PIX in item["text"] for item in executor.app.bot.sent)


@pytest.mark.asyncio
async def test_a_refused_channel_does_not_make_the_owner_wait_for_the_artifact(monkeypatch):
    """Depois da recusa, pedir o Pix responde na hora em vez de enfileirar."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            "get_equatorial_pix": ({"reference": "07/2026", "pix_payload": FAKE_PIX}, None),
        },
    )
    await executor._equatorial_bill_flow(1, "casa")
    executor.poco_calls.clear()
    executor._bill_stale_only.clear()  # isola a trava de cache desta prova

    await executor._send_bill_pix(1, "casa")

    assert job_calls(executor, "get_equatorial_pix") == []
    assert "antifraude" in executor.app.bot.sent[-1]["text"].lower()
    assert all(FAKE_PIX not in item["text"] for item in executor.app.bot.sent)


@pytest.mark.asyncio
async def test_a_slow_portal_does_not_forbid_asking_for_the_pix_again(monkeypatch):
    """Tropeço não é recusa. Bloquear o Pix por lentidão seria "não posso" falso."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_PORTAL_TIMEOUT")),
            "get_equatorial_pix": ({"reference": "07/2026", "pix_payload": FAKE_PIX}, None),
        },
    )
    await executor._equatorial_bill_flow(1, "casa")
    executor._bill_stale_only.clear()

    await executor._send_bill_pix(1, "casa")

    assert len(job_calls(executor, "get_equatorial_pix")) == 1
    assert any(FAKE_PIX in item["text"] for item in executor.app.bot.sent)


@pytest.mark.asyncio
async def test_an_artifact_is_only_reused_with_proof_that_the_bill_is_the_same(monkeypatch):
    """Referência ausente não é permissão: sem prova, busca de novo.

    Um canal que devolve valor sem referência deixava o artefato do mês anterior
    valer como o de agora, porque a checagem só barrava contradição explícita.
    """
    executor = build_executor(
        monkeypatch,
        {"get_equatorial_pix": ({"reference": "07/2026", "pix_payload": FAKE_PIX}, None)},
    )

    await executor._send_bill_pix(1, "casa")  # sem consulta antes: nada provado
    await executor._send_bill_pix(1, "casa")

    assert len(job_calls(executor, "get_equatorial_pix")) == 2


@pytest.mark.asyncio
async def test_two_simultaneous_taps_still_walk_the_chain_only_once(monkeypatch):
    """Single-flight com a cadeia no meio: nenhum canal é tentado em dobro."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED")),
            ACTIONS[PUBLIC_PAYMENT]: ({"amount": "R$ 187,90", "reference": "07/2026"}, None),
        },
        registry=EVERYTHING_WIRED,
        delays={ACTIONS[WEB_SESSION]: 0.05},
    )

    await asyncio.gather(
        executor._equatorial_bill_flow(1, "casa"),
        executor._equatorial_bill_flow(1, "casa"),
    )

    assert len(job_calls(executor, ACTIONS[WEB_SESSION])) == 1
    assert len(job_calls(executor, ACTIONS[PUBLIC_PAYMENT])) == 1


@pytest.mark.asyncio
async def test_the_owner_never_reads_the_chain_trail_in_the_logs_path(monkeypatch, caplog):
    """A trilha de canais é material de log; a tela recebe frase humana."""
    executor = build_executor(
        monkeypatch,
        {
            ACTIONS[WEB_SESSION]: (None, wire("EQUATORIAL_LOGIN_FAILED", FAKE_UC)),
            ACTIONS[CACHE]: (None, "sem cache no Poco."),
        },
    )

    with caplog.at_level("INFO"):
        await executor._equatorial_bill_flow(1, "casa")

    assert "web_session" in caplog.text  # diagnóstico existe
    assert FAKE_UC not in caplog.text  # dado da conta, não
    assert_no_channel_leak(executor)
