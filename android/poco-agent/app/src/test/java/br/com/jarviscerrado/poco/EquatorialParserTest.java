package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * Testes do parser da página da Equatorial.
 *
 * Todos os dados aqui são SINTÉTICOS. Nenhuma UC, CPF, linha digitável ou payload
 * PIX real aparece neste arquivo: os números são sequências óbvias (9999..., blocos
 * repetidos) e o PIX carrega o texto "EXEMPLOFICTICIO".
 */
public class EquatorialParserTest {

    /** Rodapé que o portal carrega em TODA página, inclusive na fatura legível. */
    private static final String PASSIVE_RECAPTCHA_FOOTER =
        "protegido por reCAPTCHA - Privacidade - Termos\n"
            + "Este site esta excedendo a cota gratuita do reCAPTCHA Enterprise";

    /** Linha digitável fictícia de concessionária: 48 dígitos com pontos e espaços. */
    private static final String FAKE_DIGITABLE_LINE =
        "8467.00000001 2345.00000002 3456.00000003 4567.00000004";

    private static final String FAKE_DIGITABLE_DIGITS =
        "846700000001" + "234500000002" + "345600000003" + "456700000004";

    /** PIX copia e cola fictício: payload EMV começando por 000201, sem dado real. */
    private static final String FAKE_PIX_PAYLOAD =
        "00020126360014BR.GOV.BCB.PIX0114EXEMPLOFICTICIO"
            + "52040000530398654041.005802BR5909ROD.TESTE6008GOIANIA62070503***6304ABCD";

    // ---------------------------------------------------------------- caso 1

    @Test
    public void completeBillExposesAmountDueDateAndReference() {
        // Caso base: é exatamente esse conjunto (valor + vencimento + referência)
        // que o ROD repassa ao dono. Se um deles sai errado, a cobrança sai errada.
        String page = "Equatorial Goias\n"
            + "Unidade Consumidora: 99999999\n"
            + "Fatura de energia eletrica\n"
            + "Mes de referencia: 07/2026\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor total: R$ 342,17\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("342,17", result.get("amount"));
        assertEquals("15/08/2026", result.get("due_date"));
        assertEquals("07/2026", result.get("reference"));
        assertEquals("99999999", result.get("uc"));
    }

    // ---------------------------------------------------------------- caso 2

    @Test
    public void digitableLineIsNormalizedToDigitsOnly() {
        // A linha digitável chega da tela com pontos e espaços. Quem paga precisa
        // dos 48 dígitos limpos; um ponto sobrando invalida a leitura no banco.
        String page = "Fatura em aberto\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor: R$ 342,17\n"
            + "Codigo de barras\n"
            + FAKE_DIGITABLE_LINE + "\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals(FAKE_DIGITABLE_DIGITS, result.get("barcode"));
        assertEquals(48, result.get("barcode").length());
    }

    // ---------------------------------------------------------------- caso 3

    @Test
    public void pixCopyAndPastePayloadIsCaptured() {
        // O PIX copia e cola é o caminho de pagamento mais usado; ele precisa sair
        // íntegro, caractere por caractere, senão o QR/código é recusado.
        String page = "Fatura em aberto\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor: R$ 342,17\n"
            + "PIX copia e cola\n"
            + FAKE_PIX_PAYLOAD + "\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals(FAKE_PIX_PAYLOAD, result.get("pix"));
        assertTrue(result.get("pix").startsWith("000201"));
    }

    // ---------------------------------------------------------------- caso 4

    @Test
    public void billWithBarcodeAndWithoutPixLeavesPixEmpty() {
        // O portal nem sempre oferece as duas formas de pagamento. Preencher o PIX
        // com lixo (ou com pedaço do código de barras) faria o ROD entregar um
        // pagamento inválido como se fosse bom.
        String page = "Fatura em aberto\n"
            + "Mes de referencia: 07/2026\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor: R$ 342,17\n"
            + "Linha digitavel\n"
            + FAKE_DIGITABLE_LINE + "\n"
            + PASSIVE_RECAPTCHA_FOOTER;

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals(FAKE_DIGITABLE_DIGITS, result.get("barcode"));
        assertEquals("", result.get("pix"));
    }

    // ---------------------------------------------------------------- caso 5

    @Test
    public void billWithPixAndWithoutBarcodeLeavesBarcodeEmpty() {
        // Espelho do caso anterior: o payload PIX contém corridas longas de dígitos
        // e não pode ser confundido com uma linha digitável de 44+ dígitos.
        String page = "Fatura em aberto\n"
            + "Mes de referencia: 07/2026\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor: R$ 342,17\n"
            + "PIX copia e cola\n"
            + FAKE_PIX_PAYLOAD + "\n"
            + PASSIVE_RECAPTCHA_FOOTER;

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals(FAKE_PIX_PAYLOAD, result.get("pix"));
        assertEquals("", result.get("barcode"));
    }

    // ---------------------------------------------------------------- caso 6

    @Test
    public void loginScreenIsReportedAsAuthRequired() {
        // Sessão caída devolve o formulário de login. Confundir isso com "sem
        // fatura" mandaria o dono procurar um problema que não existe, em vez de
        // pedir o login manual.
        String page = "Acesse sua conta\n"
            + "Unidade Consumidora *\n"
            + "CPF ou CNPJ *\n"
            + "LoginGO\n"
            + "txtUC\n"
            + "txtDocumento\n"
            + "Entrar\n"
            + PASSIVE_RECAPTCHA_FOOTER;

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.AUTH_REQUIRED, result.state);
        assertEquals("", result.get("amount"));
        assertEquals("", result.get("uc"));
    }

    // ---------------------------------------------------------------- caso 7

    @Test
    public void authenticatedPageWithoutInvoiceIsReportedAsNoBill() {
        // Estar logado e não ter fatura é resultado legítimo (conta já paga). Não
        // pode virar AUTH_REQUIRED nem inventar valores.
        String page = "Ola, cliente\n"
            + "Minhas faturas\n"
            + "Nenhuma fatura em aberto para esta unidade\n"
            + "Historico de pagamentos\n"
            + PASSIVE_RECAPTCHA_FOOTER;

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
        assertEquals("", result.get("amount"));
        assertEquals("", result.get("due_date"));
    }

    // ---------------------------------------------------------------- caso 8

    @Test
    public void realAntibotChallengeIsReportedAsHumanCheck() {
        // Bloqueio de verdade: insistir aqui queima a sessão e pode banir o IP.
        // O ROD tem que parar e avisar que precisa de gente.
        String page = "Access Denied\n"
            + "Error 15\n"
            + "Verifique que voce e humano antes de continuar\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.HUMAN_CHECK, result.state);
    }

    @Test
    public void challengeOverLoginScreenIsReportedAsHumanCheck() {
        // O desafio costuma aparecer POR CIMA do formulário de login. O bloqueio é
        // a informação acionável; classificar como AUTH_REQUIRED mandaria o ROD
        // tentar logar contra uma parede.
        String page = "Unidade Consumidora *\n"
            + "CPF ou CNPJ *\n"
            + "LoginGO\n"
            + "Verificacao de seguranca\n"
            + "Nao sou um robo\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.HUMAN_CHECK, result.state);
    }

    // ---------------------------------------------------------------- caso 9

    @Test
    public void passiveRecaptchaBadgeOnReadableBillStillReturnsBill() {
        // REGRESSÃO CRÍTICA (já quebrou em produção): o selo "protegido por
        // reCAPTCHA" e o aviso de cota do reCAPTCHA Enterprise ficam no rodapé de
        // TODA página do portal. Um parser que só procurava "captcha" declarava
        // verificação humana numa fatura perfeitamente legível e a consulta morria.
        String page = "Faturas e outros servicos\n"
            + "Unidade Consumidora: 99999999\n"
            + "Mes de referencia: 07/2026\n"
            + "Vencimento: 18/08/2026\n"
            + "R$ 210,44\n"
            + "Linha digitavel\n"
            + FAKE_DIGITABLE_LINE + "\n"
            + PASSIVE_RECAPTCHA_FOOTER;

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("210,44", result.get("amount"));
        assertEquals("18/08/2026", result.get("due_date"));
        assertEquals("07/2026", result.get("reference"));
        assertEquals(FAKE_DIGITABLE_DIGITS, result.get("barcode"));
    }

    // --------------------------------------------------------------- caso 10

    @Test
    public void amountWithoutDueDateIsNotEnoughToBeABill() {
        // Página parcial (carregamento incompleto). Valor sem vencimento não é
        // fatura: anunciar "sua conta é R$ X" sem data seria dado pela metade.
        String page = "Resumo da conta\n"
            + "Valor: R$ 87,90\n"
            + "Status: em processamento\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
        assertEquals("87,90", result.get("amount"));
        assertEquals("", result.get("due_date"));
    }

    @Test
    public void dueDateWithoutAmountIsNotEnoughToBeABill() {
        // Espelho do caso acima: vencimento sozinho também não fecha uma fatura.
        String page = "Resumo da conta\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor indisponivel no momento\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
        assertEquals("15/08/2026", result.get("due_date"));
        assertEquals("", result.get("amount"));
    }

    // --------------------------------------------------------------- caso 11

    @Test
    public void nullInputIsHandledWithoutThrowing() {
        // A coleta por acessibilidade pode devolver nada. Uma exceção aqui derruba
        // o serviço no aparelho, e não só a consulta.
        EquatorialTextParser.Page result = EquatorialTextParser.parse(null);

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
        assertEquals("", result.get("amount"));
        assertEquals("", result.get("due_date"));
        assertEquals("", result.get("barcode"));
        assertEquals("", result.get("pix"));
    }

    @Test
    public void emptyInputIsHandledWithoutThrowing() {
        // Tela ainda carregando: texto vazio é NO_BILL, nunca erro.
        EquatorialTextParser.Page result = EquatorialTextParser.parse("");

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
        assertEquals("", result.get("amount"));
        assertEquals("", result.get("reference"));
        assertEquals("", result.get("uc"));
    }

    // ------------------------------------------------------------- extras de
    // ------------------------------------------------------------- robustez

    @Test
    public void shortNumberSequenceIsNotMistakenForABarcode() {
        // Números soltos na tela (protocolo, medidor) não podem virar código de
        // barras: pagar uma linha digitável falsa é dano irreversível.
        String page = "Fatura em aberto\n"
            + "Protocolo 1234567890123456789012\n"
            + "Vencimento: 15/08/2026\n"
            + "Valor: R$ 342,17\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("", result.get("barcode"));
    }

    @Test
    public void unknownFieldIsReturnedAsEmptyStringNotNull() {
        // Quem consome a Page monta JSON direto dos campos; null viraria "null"
        // no relatório enviado ao dono.
        EquatorialTextParser.Page result = EquatorialTextParser.parse("");

        assertEquals("", result.get("campo_inexistente"));
    }

    // ------------------------------------------------- regressões comprovadas

    @Test
    public void strayNumbersOnSeparateLinesDoNotFabricateABarcode() {
        // Falha real: o padrão aceitava \s, que inclui quebra de linha, e a coleta
        // por acessibilidade junta os rótulos com "\n". Números independentes da
        // página eram colados numa linha digitável inexistente — inclusive
        // engolindo os centavos do valor como início dela.
        String page = "Vencimento: 15/08/2026\n"
            + "R$ 10,00\n"
            + "11111111\n"
            + "22222222\n"
            + "33333333\n"
            + "44444444\n"
            + "55555555\n"
            + "66666666\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals("", result.get("barcode"));
    }

    @Test
    public void labeledTotalWinsOverInterestPrintedEarlier() {
        // Falha real: pegava o primeiro "R$" da tela. Numa fatura que mostra juros
        // e multa antes do total, o dono receberia o valor dos juros como se fosse
        // a conta inteira.
        String page = "Fatura em aberto\n"
            + "Juros R$ 3,10\n"
            + "Multa R$ 1,05\n"
            + "Total a pagar R$ 342,17\n"
            + "Vencimento: 15/08/2026\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("342,17", result.get("amount"));
    }

    @Test
    public void severalUnlabeledAmountsAreRefusedInsteadOfGuessed() {
        // Ambiguidade é falha PROPOSITAL. Havendo vários valores e nenhum rotulado,
        // escolher um seria chute com o dinheiro do dono. Não "conserte" este teste
        // fazendo o parser escolher o primeiro ou o maior.
        String page = "Fatura em aberto\n"
            + "R$ 3,10\n"
            + "R$ 342,17\n"
            + "Vencimento: 15/08/2026\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals("", result.get("amount"));
        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
    }

    @Test
    public void singleUnlabeledAmountIsStillAccepted() {
        // Contraprova da regra acima: um único valor na tela continua legível, ou a
        // correção da ambiguidade teria matado o caso comum.
        String page = "Fatura em aberto\n"
            + "R$ 342,17\n"
            + "Vencimento: 15/08/2026\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("342,17", result.get("amount"));
    }

    @Test
    public void amountAboveOneThousandWithoutSeparatorIsAccepted() {
        // Falha real: o padrão exigia o ponto de milhar, então uma fatura alta
        // renderizada como "R$ 1234,56" era classificada como "sem fatura" em
        // silêncio, e o dono nunca ficava sabendo do valor.
        String page = "Total a pagar R$ 1234,56\nVencimento: 15/08/2026\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("1234,56", result.get("amount"));
    }

    @Test
    public void amountAboveOneThousandWithSeparatorIsStillAccepted() {
        String page = "Total a pagar R$ 1.234,56\nVencimento: 15/08/2026\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("1.234,56", result.get("amount"));
    }

    @Test
    public void openBillListingWithReferenceButNoDueDateIsStillABill() {
        // Falha real: a listagem de faturas em aberto do portal traz mes de
        // referencia e valor, e nenhum vencimento. Exigir vencimento fazia uma
        // fatura verdadeira ser reportada como inexistente.
        String page = "Mes/Ano de referencia Valor Download Pagamento via PIX\n"
            + "Referencia 07/2026\n"
            + "R$ 210,44\n"
            + "Download";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("210,44", result.get("amount"));
        assertEquals("07/2026", result.get("reference"));
        assertEquals("", result.get("due_date"));
    }

    @Test
    public void amountWithNoTemporalAnchorIsStillRefused() {
        // Contraprova: um R$ solto nao vira fatura. Sem vencimento nem referencia
        // nao ha como saber de que conta se trata.
        EquatorialTextParser.Page result =
            EquatorialTextParser.parse("Promocao Energia em Dia\nR$ 210,44\nSaiba mais");

        assertEquals(EquatorialTextParser.State.NO_BILL, result.state);
    }

    @Test
    public void bemobiCheckoutListingIsARealBillNotAnInDayBanner() {
        String page = "Selecione as faturas que deseja pagar\n"
            + "REF.\nVENC.\nSTATUS\nVALOR\n"
            + "Agosto\n11/08/2026\nVencido\nR$ 463,61\n"
            + "Fatura 07/2026\nTotal a pagar R$ 463,61\n"
            + "Programa Energia em Dia\n";

        EquatorialTextParser.Page result = EquatorialTextParser.parse(page);

        assertEquals(EquatorialTextParser.State.BILL, result.state);
        assertEquals("463,61", result.get("amount"));
        assertEquals("07/2026", result.get("reference"));
        assertEquals("11/08/2026", result.get("due_date"));
        assertEquals("", AgenciaWebLogin.debtNotice(EquatorialSession.fold(page)));
    }
}
