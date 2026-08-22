package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import org.junit.Test;

/**
 * A máquina de sessão em isolamento.
 *
 * Estes testes existem por dois motivos, e o segundo importa mais que o primeiro.
 * Um: provar que expirar deixou de ser o fim da consulta. Dois: travar as duas
 * regras de segurança — teto de duas tentativas de login e recusa terminal — de
 * forma que afrouxá-las quebre o build em vez de virar força bruta silenciosa
 * contra a conta do proprietário.
 */
public class EquatorialSessionTest {

    private static final String LOGIN_PAGE =
        "Agencia Virtual\nUnidade Consumidora *\nCPF ou CNPJ *\nENTRAR\nprotegido por reCAPTCHA";
    private static final String BILL_PAGE =
        "Segunda Via de Conta\nSelecione a Unidade Consumidora\n"
            + "Total a pagar: R$ 210,44\nVencimento: 18/08/2026";

    // ------------------------------------------------------------ decisões

    @Test public void expiredSessionLeadsToLoginInsteadOfFailure() {
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.LOGIN,
            session.observe(EquatorialSession.State.SESSION_EXPIRED));
        assertEquals(1, session.loginAttempts());
    }

    @Test public void validSessionProceeds() {
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.PROCEED,
            session.observe(EquatorialSession.State.SESSION_VALID));
        assertEquals(EquatorialSession.Decision.PROCEED,
            session.observe(EquatorialSession.State.LOGIN_OK));
        // Prosseguir não gasta tentativa de login: só autenticar gasta.
        assertEquals(0, session.loginAttempts());
    }

    @Test public void aReloadedFormIsRefilledAtMostFourTimes() {
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.LOGIN,
            session.observe(EquatorialSession.State.SESSION_EXPIRED));
        for (int i = 0; i < EquatorialSession.MAX_TRANSIENT_REFILLS; i++)
            assertEquals(EquatorialSession.Decision.LOGIN,
                session.observe(EquatorialSession.State.LOGIN_IN_PROGRESS));
        // Quinta reaparição: acabou. Não há laço infinito.
        assertEquals(EquatorialSession.Decision.FALLBACK_WEBVIEW,
            session.observe(EquatorialSession.State.LOGIN_IN_PROGRESS));
        session.markWebViewTried();
        assertEquals(EquatorialSession.Decision.FAIL_EXHAUSTED,
            session.observe(EquatorialSession.State.LOGIN_IN_PROGRESS));
        assertEquals(1, session.loginAttempts());
        assertEquals(EquatorialSession.MAX_TRANSIENT_REFILLS, session.transientRefills());
    }

    @Test public void anExplicitRejectionStillStopsBeforeAnyRefill() {
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.FAIL_LOGIN_REJECTED,
            session.observe(EquatorialSession.State.LOGIN_REJECTED));
        assertEquals(0, session.transientRefills());
    }

    @Test public void rejectedCredentialsStopImmediatelyAndDoNotTryTheOtherEngine() {
        // Repetir credencial recusada é força bruta contra a conta do dono, mesmo
        // que involuntária, e trocar de motor não muda isso.
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.FAIL_LOGIN_REJECTED,
            session.observe(EquatorialSession.State.LOGIN_REJECTED));
        assertEquals(EquatorialSession.Decision.FAIL_LOGIN_REJECTED,
            session.observe(EquatorialSession.State.LOGIN_REJECTED));
        assertEquals(0, session.loginAttempts());
        assertTrue(EquatorialSession.terminal(EquatorialSession.Decision.FAIL_LOGIN_REJECTED));
    }

    @Test public void withoutCredentialsThereIsNothingToTry() {
        EquatorialSession session = new EquatorialSession(false);
        assertEquals(EquatorialSession.Decision.FAIL_NO_CREDENTIALS,
            session.observe(EquatorialSession.State.SESSION_EXPIRED));
        assertEquals(0, session.loginAttempts());
    }

    @Test public void humanCheckFailsOverOnceAndThenStops() {
        // Failover legítimo: o outro motor pode não receber o desafio. Se receber,
        // acabou — nada de solucionador, nada de token fabricado.
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.FALLBACK_WEBVIEW,
            session.observe(EquatorialSession.State.HUMAN_CHECK));
        session.markWebViewTried();
        assertEquals(EquatorialSession.Decision.FAIL_HUMAN_CHECK,
            session.observe(EquatorialSession.State.HUMAN_CHECK));
    }

    @Test public void browserRecoveryClimbsOneStepAtATime() {
        EquatorialSession session = new EquatorialSession(true);
        assertEquals(EquatorialSession.Decision.RELOAD_ROUTE,
            session.observe(EquatorialSession.State.BROWSER_STALE));
        assertEquals(EquatorialSession.Decision.REOPEN_TAB,
            session.observe(EquatorialSession.State.BROWSER_STALE));
        assertEquals(EquatorialSession.Decision.FALLBACK_WEBVIEW,
            session.observe(EquatorialSession.State.BROWSER_STALE));
        session.markWebViewTried();
        assertEquals(EquatorialSession.Decision.FAIL_EXHAUSTED,
            session.observe(EquatorialSession.State.BROWSER_STALE));
        assertEquals(EquatorialSession.MAX_BROWSER_RECOVERIES, session.browserRecoveries());
    }

    @Test public void everyTerminalOutcomeCarriesATypedCode() {
        for (EquatorialSession.Decision decision : EquatorialSession.Decision.values()) {
            if (!EquatorialSession.terminal(decision)) continue;
            String message = EquatorialSession.errorFor(decision);
            assertTrue(decision + " sem codigo tipado: " + message,
                message.startsWith("EQUATORIAL_"));
        }
    }

    // --------------------------------------------------------- classificação

    @Test public void billSelectorMeansAuthenticatedEvenWithLoginWordsOnThePage() {
        // O rodapé do portal fala de login em toda página, inclusive nas
        // autenticadas. Decidir por texto já reportou sessão expirada com sessão
        // viva, que é o pior erro: manda resolver um problema que não existe.
        String page = BILL_PAGE + "\nEntrar\nAgencia Virtual\nCPF ou CNPJ *";
        assertEquals(EquatorialSession.State.SESSION_VALID,
            EquatorialSession.classify(page, true, true, false, false));
        assertEquals(EquatorialSession.State.LOGIN_OK,
            EquatorialSession.classify(page, true, true, false, true));
    }

    @Test public void loginScreenBeforeSubmitIsAnExpiredSession() {
        assertEquals(EquatorialSession.State.SESSION_EXPIRED,
            EquatorialSession.classify(LOGIN_PAGE, true, false, true, false));
    }

    @Test public void loginScreenAfterSubmitWithoutAVerdictKeepsGoing() {
        // Sem mensagem de recusa não se pode afirmar credencial errada. O estado é
        // "em andamento", e é o teto de tentativas que encerra o job.
        assertEquals(EquatorialSession.State.LOGIN_IN_PROGRESS,
            EquatorialSession.classify(LOGIN_PAGE, true, false, true, true));
    }

    @Test public void portalSayingTheDataDoesNotMatchIsARejection() {
        String rejected = LOGIN_PAGE + "\nOs dados informados não conferem";
        assertEquals(EquatorialSession.State.LOGIN_REJECTED,
            EquatorialSession.classify(rejected, true, false, true, true));
        // Antes do envio a mesma tela é só sessão caída: a mensagem pode ser resto
        // de outra visita, e recusa é terminal demais para se afirmar por engano.
        assertEquals(EquatorialSession.State.SESSION_EXPIRED,
            EquatorialSession.classify(rejected, true, false, true, false));
    }

    @Test public void invalidUnitAndWrongHolderAreAlsoRejections() {
        assertEquals(EquatorialSession.State.LOGIN_REJECTED,
            EquatorialSession.classify(LOGIN_PAGE + "\nUnidade consumidora inválida",
                true, false, true, true));
        assertEquals(EquatorialSession.State.LOGIN_REJECTED,
            EquatorialSession.classify(LOGIN_PAGE + "\nO documento informado não é o titular",
                true, false, true, true));
    }

    @Test public void ordinaryLoginHelpTextIsNotARejection() {
        // "Informe o CPF do titular" é instrução da própria tela. Ler isso como
        // recusa encerraria o job na primeira tentativa e o auto-login morreria.
        String helpful = LOGIN_PAGE + "\nInforme o CPF do titular da unidade consumidora\n"
            + "Verifique se o teclado está correto";
        assertEquals(EquatorialSession.State.LOGIN_IN_PROGRESS,
            EquatorialSession.classify(helpful, true, false, true, true));
    }

    @Test public void humanCheckWinsOverTheLoginScreen() {
        // O desafio pode aparecer sobre a tela de login, e nesse caso o problema
        // não é a credencial: insistir com ela seria pior que parar.
        String blocked = LOGIN_PAGE + "\nAccess Denied\nError 15";
        assertEquals(EquatorialSession.State.HUMAN_CHECK,
            EquatorialSession.classify(blocked, true, false, true, true));
    }

    @Test public void thePortalErrorPageMeansTheSessionIsGone() {
        // Descoberto no aparelho: sem sessão, a rota da segunda via não devolve a
        // tela de login — ela estoura no servidor e cai em Suporte.aspx. Lido como
        // navegador ruim, isso gastava as duas recuperações de navegador e nunca
        // chegava ao login, que é o que resolve.
        String outage = "Sair\nSistema Indisponível\nPrezado cliente,\n"
            + "Nosso sistema encontra-se indisponível no momento.";
        assertEquals(EquatorialSession.State.SESSION_EXPIRED,
            EquatorialSession.classify(outage, true, false, false, false));
        assertEquals(EquatorialSession.State.LOGIN_IN_PROGRESS,
            EquatorialSession.classify(outage, true, false, false, true));
    }

    @Test public void beingInsideThePortalButOffRouteIsStillAValidSession() {
        // Entrar desemboca na home da área logada, não na segunda via. O portal só
        // oferece "Sair" para quem entrou, e a tela de login não tem esse link.
        String home = "Minha Conta\nFaturas e outros servicos\nSair";
        assertEquals(EquatorialSession.State.LOGIN_OK,
            EquatorialSession.classify(home, true, false, false, true));
        assertEquals(EquatorialSession.State.SESSION_VALID,
            EquatorialSession.classify(home, true, false, false, false));
    }

    @Test public void theErrorPageHeaderDoesNotPassAsAuthenticated() {
        // A página de erro renderiza o cabeçalho da área logada, "Sair" incluído.
        // Se ela fosse lida como autenticada, o fluxo seguiria para uma tela que
        // não tem fatura nenhuma em vez de reautenticar.
        String outage = "Sair\nSistema Indisponível";
        assertEquals(EquatorialSession.State.SESSION_EXPIRED,
            EquatorialSession.classify(outage, true, false, false, false));
    }

    @Test public void nothingOnScreenMeansTheBrowserIsStale() {
        assertEquals(EquatorialSession.State.BROWSER_STALE,
            EquatorialSession.classify("", false, false, false, false));
        assertEquals(EquatorialSession.State.BROWSER_STALE,
            EquatorialSession.classify("Erro de conexao\nSem internet", true, false, false, false));
    }

    @Test public void aBillWithoutTheSelectorStillCountsAsAuthenticated() {
        assertEquals(EquatorialSession.State.SESSION_VALID,
            EquatorialSession.classify(
                "Total a pagar: R$ 210,44\nVencimento: 18/08/2026", true, false, false, false));
    }

    @Test public void accentsDoNotChangeTheReading() {
        assertEquals("nao conferem", EquatorialSession.fold("Não Conferem"));
        assertEquals(EquatorialSession.State.LOGIN_REJECTED,
            EquatorialSession.classify(LOGIN_PAGE + "\nDADOS NAO CONFEREM",
                true, false, true, true));
    }

    // ------------------------------------------------------------- unidades

    @Test public void thePortalWritesTheUnitWithLeadingZeros() {
        assertTrue(EquatorialSession.sameUnit("000012345678901", "12345678901"));
        assertFalse(EquatorialSession.sameUnit("12345678902", "12345678901"));
        assertFalse(EquatorialSession.sameUnit("", "12345678901"));
        assertFalse(EquatorialSession.sameUnit("12345678901", ""));
    }

    @Test public void theSecondLoginTriesThePortalSpellingOfTheSameUnit() {
        // Conferido no aparelho: o portal nao devolve mensagem nenhuma quando recusa
        // o acesso — uma tentativa com unidade inexistente produz exatamente a mesma
        // tela em branco que uma tentativa legitima. Sem sinal, a segunda tentativa
        // escreve o MESMO numero como o portal o escreve: quinze digitos.
        assertEquals("000012345678901", EquatorialReader.portalUnit("12345678901"));
        assertEquals("000012345678901", EquatorialReader.portalUnit("1.234.567-8901"));
        // Ja com quinze digitos, nada muda: seria outro numero.
        assertEquals("000012345678901", EquatorialReader.portalUnit("000012345678901"));
        assertEquals("", EquatorialReader.portalUnit(""));
        assertEquals(EquatorialReader.PORTAL_UNIT_DIGITS,
            EquatorialReader.portalUnit("12345678901").length());
        // E continua sendo a mesma unidade para efeito de conferencia da fatura.
        assertTrue(EquatorialSession.sameUnit(
            EquatorialReader.portalUnit("12345678901"), "12345678901"));
    }

    // ------------------------------------------------- ciclo de reautenticação

    @Test public void anAuthErrorFromAnyStepFeedsBackIntoTheStateMachine() {
        // O passo de leitura descobre a sessão caída antes de qualquer probe.
        // Reconhecer o código é o que fecha o ciclo em vez de propagar o erro.
        assertTrue(EquatorialReader.expired(
            "IllegalStateException: EQUATORIAL_AUTH_REQUIRED: a sessao expirou"));
        assertFalse(EquatorialReader.expired(
            "IllegalStateException: EQUATORIAL_BILL_NOT_FOUND: nada na tela"));
        assertFalse(EquatorialReader.expired(null));
    }

    @Test public void theWebViewEngineIsOnlyTriedWhileTheJobStillHasTime() {
        // Começar o motor alternativo com dez segundos de orçamento gastaria a
        // última chance do job para não terminar.
        assertTrue(EquatorialReader.WEBVIEW_MIN_MILLIS > 0);
        assertTrue(EquatorialReader.JOB_BUDGET_MILLIS > EquatorialReader.WEBVIEW_MIN_MILLIS);
        // O orçamento do Android termina antes do prazo do Pi (240 s), para que o
        // erro devolvido seja tipado em vez de "o job expirou".
        assertTrue(EquatorialReader.JOB_BUDGET_MILLIS < 240_000L);
    }

    @Test public void everyRoundOfTheLoopEitherSpendsAnAttemptOrEnds() {
        // Teto do laço maior que a soma das tentativas possíveis, para que o fim
        // venha de uma decisão terminal e não de o laço estourar.
        int worstCase = EquatorialSession.MAX_LOGIN_ATTEMPTS
            + EquatorialSession.MAX_TRANSIENT_REFILLS
            + EquatorialSession.MAX_BROWSER_RECOVERIES + 2;
        assertTrue(EquatorialReader.MAX_ROUNDS >= worstCase);
    }
}
