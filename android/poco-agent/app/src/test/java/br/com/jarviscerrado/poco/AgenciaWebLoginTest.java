package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * Contrato do login da Agência Web ({@code go.*}).
 *
 * Nenhum dado real. O CPF usado tem dígitos verificadores válidos e é o valor
 * clássico de teste; a conta contrato é inventada.
 *
 * O que estes testes protegem: as regras abaixo foram lidas do fonte de
 * {@code auth-go.js} em produção, e cada uma delas, se adivinhada, falha em
 * silêncio — o portal responde a MESMA mensagem genérica para campo vazio,
 * credencial errada e antifraude reprovado, então um erro de mapeamento aqui
 * seria indistinguível de "a Equatorial recusou".
 */
public class AgenciaWebLoginTest {

    private static final String FAKE_CPF = "111.444.777-35";

    // ------------------------------------------------- documento

    @Test
    public void documentIsStrippedTheWayThePageStripsIt() {
        // O script remove ponto, hifen, barra e espaco, e sobe para maiusculas.
        assertEquals("11144477735", AgenciaWebLogin.document(FAKE_CPF));
        assertEquals("11144477735", AgenciaWebLogin.document(" 111 444 777 35 "));
        assertEquals("11222333000181", AgenciaWebLogin.document("11.222.333/0001-81"));
    }

    @Test
    public void alphanumericCnpjKeepsItsLetters() {
        // A linha que apagava todo nao-digito esta COMENTADA no fonte, e CNPJ
        // alfanumerico existe: apagar letra aqui viraria recusa inexplicavel.
        assertEquals("12ABC34501DE35", AgenciaWebLogin.document("12.ABC.345/01DE-35"));
        assertEquals("12ABC34501DE35", AgenciaWebLogin.document("12abc34501de35"));
    }

    @Test
    public void missingDocumentIsEmptyNotNull() {
        assertEquals("", AgenciaWebLogin.document(null));
        assertEquals("", AgenciaWebLogin.document(""));
    }

    // ------------------------------------------------- conta contrato

    @Test
    public void unitIsZeroPaddedToFifteen() {
        assertEquals("000012345678901", AgenciaWebLogin.unit("12345678901"));
        assertEquals(15, AgenciaWebLogin.unit("12345678901").length());
        // Separador vindo do cofre nao deve virar parte do identificador.
        assertEquals("000012345678901", AgenciaWebLogin.unit("123.456.789-01"));
    }

    @Test
    public void legacyEightDigitGoUnitIsSentExactlyAsPrinted() {
        assertEquals("12345678", AgenciaWebLogin.unit("12.345.678"));
    }

    @Test
    public void anAlreadyLongUnitIsNeverTruncated() {
        // Cortar identificador e consultar a conta de outra pessoa. Melhor o
        // portal recusar o valor do cofre do que o ROD acertar a conta errada.
        assertEquals("1234567890123456", AgenciaWebLogin.unit("1234567890123456"));
        assertEquals("123456789012345", AgenciaWebLogin.unit("123456789012345"));
    }

    @Test
    public void emptyUnitStaysEmptyInsteadOfBecomingFifteenZeros() {
        // Quinze zeros seriam um identificador de aparencia valida.
        assertEquals("", AgenciaWebLogin.unit(""));
        assertEquals("", AgenciaWebLogin.unit(null));
        assertEquals("", AgenciaWebLogin.unit("sem digito"));
    }

    // ------------------------------------------------- serviço

    @Test
    public void serviceComesFromTheSecondPathSegment() {
        assertEquals("emitir-segunda-via",
            AgenciaWebLogin.serviceFor("/sua-conta/emitir-segunda-via/"));
        assertEquals("fazer-reclamacao",
            AgenciaWebLogin.serviceFor("/sua-conta/fazer-reclamacao/"));
    }

    @Test
    public void theBarePortalHasNoServiceAndLandsOnTheLoggedHome() {
        assertEquals("", AgenciaWebLogin.serviceFor("/sua-conta/"));
        assertEquals("", AgenciaWebLogin.serviceFor("/sua-conta"));
        assertEquals("", AgenciaWebLogin.serviceFor("/"));
        assertEquals("", AgenciaWebLogin.serviceFor(null));
    }

    // ------------------------------------------------- prontidão

    @Test
    public void anIncompleteFormIsNotSent() {
        // Enviar vazio gasta uma das duas tentativas do job e volta com a
        // mesma mensagem de credencial errada: diagnostico envenenado.
        assertFalse(AgenciaWebLogin.ready("", "12345678901"));
        assertFalse(AgenciaWebLogin.ready(FAKE_CPF, ""));
        assertFalse(AgenciaWebLogin.ready(null, null));
        assertFalse(AgenciaWebLogin.ready("123", "12345678901"));
        assertTrue(AgenciaWebLogin.ready(FAKE_CPF, "12345678901"));
        assertTrue(AgenciaWebLogin.ready(FAKE_CPF, "12345678"));
    }

    // ------------------------------------------------- desfecho

    @Test
    public void jwtIsTheProofOfSession() {
        assertEquals(AgenciaWebLogin.Outcome.AUTHENTICATED,
            AgenciaWebLogin.classify(true, false, false));
        // JWT vence erro na tela: o script so grava o token depois de um 200.
        assertEquals(AgenciaWebLogin.Outcome.AUTHENTICATED,
            AgenciaWebLogin.classify(true, true, true));
    }

    @Test
    public void aVisibleErrorBoxIsRefusalAndAHiddenOneIsNot() {
        assertEquals(AgenciaWebLogin.Outcome.REFUSED,
            AgenciaWebLogin.classify(false, true, true));
        // A caixa fica sempre no DOM; presenca nao e veredito.
        assertEquals(AgenciaWebLogin.Outcome.PENDING,
            AgenciaWebLogin.classify(false, false, true));
    }

    @Test
    public void noFormAndNoJwtIsNotAVerdict() {
        assertEquals(AgenciaWebLogin.Outcome.UNKNOWN,
            AgenciaWebLogin.classify(false, false, false));
    }

    // ------------------------------------------------- alvos proibidos

    @Test
    public void theDismissControlIsNotTheConsentControl() {
        // Fechar o aviso e consentir com ele nao sao a mesma acao, e o ROD nao
        // tem autorizacao para consentir em nome do proprietario.
        assertFalse(AgenciaWebLogin.LGPD_CLOSE.equals(AgenciaWebLogin.LGPD_ACCEPT));
        assertTrue(AgenciaWebLogin.LGPD_ACCEPT.contains("lgpd_accept"));
        assertFalse(AgenciaWebLogin.LGPD_CLOSE.contains("lgpd_accept"));
    }

    @Test
    public void theUnitGoesInTheFieldTheHandlerActuallyReads() {
        // O handler monta uc a partir do FormData 'senha'. Os campos
        // 'contrato-novo' e '#identificador-2' existem, estao invisiveis e nao
        // sao lidos: preencher aqueles manda a UC vazia com cara de completo.
        assertEquals("#senha-identificador", AgenciaWebLogin.FIELD_UNIT);
        assertEquals("#identificador", AgenciaWebLogin.FIELD_DOCUMENT);
    }
    // ------------------------------------------------- ponte entre os hosts

    @Test
    public void theBridgeIsOpenOnlyWithEveryBillControlPresent() {
        // Postback parcial do ASPX ja devolveu combo sem botao. Aceitar "quase"
        // como ponte aberta faria o ROD prometer consulta que nao completa.
        assertEquals(AgenciaWebLogin.BridgeState.OPEN, AgenciaWebLogin.bridge(
            true, false, true, true, true, true, true));
        assertEquals(AgenciaWebLogin.BridgeState.CLOSED, AgenciaWebLogin.bridge(
            true, false, true, true, true, false, true));
        assertEquals(AgenciaWebLogin.BridgeState.CLOSED, AgenciaWebLogin.bridge(
            true, false, false, true, true, true, true));
    }

    @Test
    public void aVisibleLoginFormMeansTheSessionDidNotCross() {
        // Se o cabecalho do ASPX volta a pedir credencial, a sessao do go.* nao
        // valeu ali — mesmo que a pagina tenha carregado inteira.
        assertEquals(AgenciaWebLogin.BridgeState.CLOSED, AgenciaWebLogin.bridge(
            true, true, true, true, true, true, true));
    }

    @Test
    public void aBlankBillPageIsItsOwnState() {
        // Pagina que nao veio de todo: sem controles, sem login, sem texto.
        // NAO e a assinatura de "sem sessao" — aquela e o redirecionamento para
        // a pagina de aviso, que TEM texto. Este estado cobre render que falhou,
        // rede que caiu, motor que nao pintou. Confundir os dois mandaria o dono
        // procurar defeito de credencial quando nao chegou resposta nenhuma.
        assertEquals(AgenciaWebLogin.BridgeState.BLANK, AgenciaWebLogin.bridge(
            true, false, false, false, false, false, false));
    }

    @Test
    public void withoutALoginOnGoThereIsNothingToMeasure() {
        // Medir a ponte sem ter autenticado seria concluir por coincidencia.
        assertEquals(AgenciaWebLogin.BridgeState.NOT_TESTED, AgenciaWebLogin.bridge(
            false, false, true, true, true, true, true));
    }

    @Test
    public void theServiceWordIsGenericSoTheLabelNeverLeaks() {
        // O menu autenticado costuma trazer o nome do titular no texto do link.
        // O que vai para a trilha e a palavra do vocabulario, nunca o rotulo.
        assertEquals("segunda via", AgenciaWebLogin.serviceWord("segunda via de fatura"));
        assertEquals("agencia virtual", AgenciaWebLogin.serviceWord("acesse a agencia virtual"));
        assertEquals("", AgenciaWebLogin.serviceWord("trabalhe conosco"));
        assertEquals("", AgenciaWebLogin.serviceWord(null));
    }

    @Test
    public void theBillHostIsTheAspxHostNotTheInstitutionalOne() {
        assertEquals("goias.equatorialenergia.com.br", AgenciaWebLogin.BILL_HOST);
        assertFalse(AgenciaWebLogin.LOGIN_URL.contains(AgenciaWebLogin.BILL_HOST));
    }

    @Test
    public void officialAppReadContractIsGoiasAndReadOnly() {
        // Contrato conferido no APK oficial instalado, sem unidade real. A rota
        // só lista faturas em aberto; não aponta para checkout nem pagamento.
        assertEquals("https://api06.equatorialenergia.com.br/bff-go",
            EquatorialWebEngine.GO_APP_BFF);
        assertEquals("/api/v1/faturas/em-aberto/",
            EquatorialWebEngine.GO_OPEN_BILLS_PATH);
        assertEquals("/api/v1/debitos/", EquatorialWebEngine.GO_DEBTS_PATH);
        assertEquals("/api/v2/clientes/", EquatorialWebEngine.GO_CUSTOMER_ACCOUNTS_PATH);
        assertEquals("/api/v1/instalacao/", EquatorialWebEngine.GO_INSTALLATION_PATH);
        assertFalse(EquatorialWebEngine.GO_OPEN_BILLS_PATH.contains("pagamento"));
        assertFalse(EquatorialWebEngine.GO_OPEN_BILLS_PATH.contains("checkout"));
    }


    // ------------------------------------------------- vocabulario do relatorio

    @Test
    public void anOpaqueRefusalIsReportedAsRefusalAndNothingMore() {
        // O auth-go.js escreve a MESMA mensagem para qualquer status != 200, e
        // por isso credencial errada e reCAPTCHA reprovado nao existem como
        // estados aqui: a pagina nao contem a informacao que os separaria.
        assertEquals(AgenciaWebLogin.GoOutcome.GO_LOGIN_REJECTED,
            AgenciaWebLogin.outcome(EquatorialSession.State.LOGIN_REFUSED_OPAQUE));
    }

    @Test
    public void bothProofsOfSessionCountAsLoginOk() {
        // JWT depois do envio e JWT achado ja na abertura provam a mesma coisa.
        assertEquals(AgenciaWebLogin.GoOutcome.GO_LOGIN_OK,
            AgenciaWebLogin.outcome(EquatorialSession.State.LOGIN_OK));
        assertEquals(AgenciaWebLogin.GoOutcome.GO_LOGIN_OK,
            AgenciaWebLogin.outcome(EquatorialSession.State.SESSION_VALID));
    }

    @Test
    public void noVerdictWithinTheDeadlineIsTimeoutNotRefusal() {
        // Chamar de recusa o que foi prazo curto culparia a Equatorial por um
        // defeito nosso.
        assertEquals(AgenciaWebLogin.GoOutcome.GO_TIMEOUT,
            AgenciaWebLogin.outcome(EquatorialSession.State.LOGIN_IN_PROGRESS));
        assertEquals(AgenciaWebLogin.GoOutcome.GO_PORTAL_ERROR,
            AgenciaWebLogin.outcome(EquatorialSession.State.BROWSER_STALE));
    }
    @Test
    public void theNoticeWordNamesTheRefusalWithoutCopyingThePage() {
        // O SegundaVia.aspx sem sessao nao devolve formulario de login: ele
        // redireciona para uma pagina curta. Guardar a PALAVRA do vocabulario
        // diz ao dono por que a area recusou sem levar texto de pagina para o log.
        assertEquals("suporte", AgenciaWebLogin.noticeWord("pagina de suporte"));
        assertEquals("sessao expirada", AgenciaWebLogin.noticeWord("sua sessao expirada"));
        assertEquals("", AgenciaWebLogin.noticeWord("segunda via emitida"));
        assertEquals("", AgenciaWebLogin.noticeWord(null));
    }

    @Test
    public void theNoticeVocabularyExpectsFoldedText() {
        // A comparacao roda sobre texto sem acento e em minusculas; palavra com
        // acento no vocabulario nunca casaria e o aviso viraria vazio silencioso.
        for (String word : AgenciaWebLogin.BILL_NOTICE_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
        for (String word : AgenciaWebLogin.SERVICE_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
    }

    // ------------------------------------------------- freio do funil de cartao

    @Test
    public void onlyTheStepThatListsTheDebtMayBeClicked() {
        // "Continuar" troca unidade por lista de debito, e listar e leitura.
        assertTrue(AgenciaWebLogin.safeToAdvance("continuar"));
        assertTrue(AgenciaWebLogin.safeToAdvance(" continuar "));
    }

    @Test
    public void aPaymentWordVetoesTheClickEvenNextToContinuar() {
        // Esta e a regra que protege o dinheiro do dono: a proibicao vence o
        // consentimento. Um botao que diz "continuar" E "pagamento" e um botao
        // de pagamento com rotulo simpatico.
        assertFalse(AgenciaWebLogin.safeToAdvance("continuar para pagamento"));
        assertFalse(AgenciaWebLogin.safeToAdvance("continuar com cartao"));
        assertFalse(AgenciaWebLogin.safeToAdvance("pagar agora"));
        assertFalse(AgenciaWebLogin.safeToAdvance("pagar com pix"));
        assertFalse(AgenciaWebLogin.safeToAdvance("finalizar compra"));
        assertFalse(AgenciaWebLogin.safeToAdvance("confirmar"));
        assertFalse(AgenciaWebLogin.safeToAdvance("parcelar"));
    }

    @Test
    public void anUnknownLabelIsNotClicked() {
        // O freio e uma lista de permissao, nao de proibicao: botao que ninguem
        // previu fica parado, porque o custo de nao clicar e uma medicao
        // incompleta e o custo de clicar pode ser uma cobranca.
        assertFalse(AgenciaWebLogin.safeToAdvance("avancar"));
        assertFalse(AgenciaWebLogin.safeToAdvance("ok"));
        assertFalse(AgenciaWebLogin.safeToAdvance(""));
        assertFalse(AgenciaWebLogin.safeToAdvance(null));
    }

    @Test
    public void thePaymentVocabularyExpectsFoldedText() {
        for (String word : AgenciaWebLogin.PAYMENT_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
        for (String word : AgenciaWebLogin.ADVANCE_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
    }

    // ------------------------------------------------- consulta x pagamento

    @Test
    public void aQueryNeedsBothAmountAndReference() {
        // Valor sem referencia nao diz de qual mes e a conta; referencia sem
        // valor nao diz quanto pagar. Metade disso manda o dono conferir no
        // portal assim mesmo, que e o trabalho manual que queremos eliminar.
        assertEquals(AgenciaWebLogin.ReadProvider.READ_PROVIDER_OK,
            AgenciaWebLogin.readProvider(true, true));
        assertEquals(AgenciaWebLogin.ReadProvider.READ_PROVIDER_UNAVAILABLE,
            AgenciaWebLogin.readProvider(true, false));
        assertEquals(AgenciaWebLogin.ReadProvider.READ_PROVIDER_UNAVAILABLE,
            AgenciaWebLogin.readProvider(false, true));
        assertEquals(AgenciaWebLogin.ReadProvider.READ_PROVIDER_UNAVAILABLE,
            AgenciaWebLogin.readProvider(false, false));
    }

    @Test
    public void cosmeticTextChangesDoNotPretendTheDebtStepAdvanced() {
        // Banner de cookies e reCAPTCHA alteram caracteres, mas não aparecem
        // neste contrato: com o seletor ainda presente e sem conteúdo de conta,
        // a espera precisa continuar.
        assertFalse(AgenciaWebLogin.debtStepChanged(1, 1, 0, 0, false));
    }

    @Test
    public void debtContentOrLeavingTheSelectorEndsTheWait() {
        assertTrue(AgenciaWebLogin.debtStepChanged(1, 0, 0, 0, false));
        assertTrue(AgenciaWebLogin.debtStepChanged(1, 1, 1, 0, false));
        assertTrue(AgenciaWebLogin.debtStepChanged(1, 1, 0, 1, false));
        assertTrue(AgenciaWebLogin.debtStepChanged(1, 1, 0, 0, true));
    }

    @Test
    public void anAccountWithNothingOwedIsAnAnswerAndNotAFailure() {
        // Sem esta distincao, dono com a conta paga receberia "o canal nao
        // funciona" e mandaria consertar o que ja estava certo.
        // Casa a forma curta mesmo quando a pagina escreve o plural: guardar as
        // duas deixaria a segunda inalcancavel, porque a primeira e prefixo dela.
        assertEquals("nao ha debito", AgenciaWebLogin.debtNotice(
            "no momento nao ha debitos para esta unidade"));
        assertEquals("sua conta esta em dia", AgenciaWebLogin.debtNotice("sua conta esta em dia"));
        assertEquals("", AgenciaWebLogin.debtNotice("programa energia em dia"));
        assertEquals("", AgenciaWebLogin.debtNotice("seu debito vence em 10 dias"));
        assertEquals("", AgenciaWebLogin.debtNotice(null));
    }

    @Test
    public void aLabelOutsideTheVocabularyIsLoggedAsSizeOnly() {
        // O cabecalho desta area comeca com uma saudacao que traz o nome do
        // titular. Rotulo desconhecido sai como tamanho, nunca por extenso.
        assertEquals("continuar", AgenciaWebLogin.publicLabel("continuar"));
        assertEquals("continuar para pagamento",
            AgenciaWebLogin.publicLabel("continuar para pagamento"));
        assertEquals("outro(10 chars)", AgenciaWebLogin.publicLabel("ola, fulano"
            .substring(0, 10)));
        assertEquals("vazio", AgenciaWebLogin.publicLabel(""));
        assertEquals("vazio", AgenciaWebLogin.publicLabel(null));
    }

    @Test
    public void theDebtVocabularyExpectsFoldedText() {
        for (String word : AgenciaWebLogin.DEBT_NOTICE_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
        for (String word : AgenciaWebLogin.PUBLIC_LABEL_WORDS) {
            assertEquals(word, EquatorialSession.fold(word));
        }
    }

    @Test
    public void theDebtRouteIsTheFunnelPathAndNotAnAjaxEndpoint() {
        // O alcance e por navegacao oficial. Guardar o caminho da PAGINA, e nao
        // o do ajax, e o que impede a proxima pessoa de "otimizar" o motor
        // chamando o endpoint interno direto.
        assertEquals("/pagamento-de-faturas-on-line/", AgenciaWebLogin.DEBTS_PATH);
        assertFalse(AgenciaWebLogin.DEBTS_PATH.contains("ajax"));
    }
    // ------------------------------------------- criterio de aceite do rerun

    @Test
    public void reachingTheBillHostMeansAPageThatIsNotTheNoticeStub() {
        // LoginGO.aspx e o host de faturas respondendo de verdade: prova que a
        // execucao bateu na porta, mesmo sem sessao.
        assertTrue(AgenciaWebLogin.reachedBillHost(
            "goias.equatorialenergia.com.br/LoginGO.aspx"));
        assertTrue(AgenciaWebLogin.reachedBillHost(
            "goias.equatorialenergia.com.br/AgenciaGO/Servicos/aberto/SegundaVia.aspx"));
    }

    @Test
    public void theNoticeStubDoesNotCountAsHavingArrived() {
        // Suporte.aspx e para onde o servidor manda quem nao tem sessao. Aceitar
        // isso como "cheguei" apagaria a diferenca entre "a sessao nao vale aqui"
        // e "o experimento nunca chegou" — que e a duvida toda.
        assertFalse(AgenciaWebLogin.reachedBillHost(
            "goias.equatorialenergia.com.br/Suporte.aspx"));
    }

    @Test
    public void anotherHostIsNeverProofOfReachingTheBillHost() {
        assertFalse(AgenciaWebLogin.reachedBillHost(
            "go.equatorialenergia.com.br/pagamento-de-faturas-on-line/"));
        assertFalse(AgenciaWebLogin.reachedBillHost("equatorialservicos.com.br/"));
        assertFalse(AgenciaWebLogin.reachedBillHost("equatorialgoias.com.br/LoginGO.aspx"));
        assertFalse(AgenciaWebLogin.reachedBillHost(""));
        assertFalse(AgenciaWebLogin.reachedBillHost(null));
    }
}
