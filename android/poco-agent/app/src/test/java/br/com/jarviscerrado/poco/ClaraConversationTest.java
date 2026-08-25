package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import org.junit.Test;

/** Conversas sintéticas: nenhum identificador ou dado financeiro real. */
public class ClaraConversationTest {
    @Test public void registrationScreenIsNotMistakenForAChat() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.OPENING, "Bem-vindo(a) ao WhatsApp\nCONCORDAR E CONTINUAR");
        assertEquals(ClaraConversation.Kind.NOT_REGISTERED, action.kind);
    }

    @Test public void startsWithAPlainGreeting() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.OPENING, "Equatorial Goiás\nMensagem");
        assertEquals(ClaraConversation.Kind.MESSAGE, action.kind);
        assertEquals("HELLO", action.value);
    }

    @Test public void typoAndAccentDoNotHideUnitPrompt() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.IDENTIFYING, "Por favor, informe a Unidade Consumidora (UC)");
        assertEquals(ClaraConversation.Kind.MESSAGE, action.kind);
        assertEquals("UNIT", action.value);
    }

    @Test public void noDebtIsARealDefinitiveResult() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.IDENTIFYING, "Tudo em dia! Não há débitos para esta UC.");
        assertEquals(ClaraConversation.Kind.NO_DEBT, action.kind);
    }

    @Test public void genericInstructionDoesNotBecomeAFalseNoDebtResult() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.IDENTIFYING,
            "Selecione uma opção para consultar se possui débitos ou pedir segunda via.");
        assertEquals(ClaraConversation.Kind.WAIT, action.kind);
    }

    @Test public void labeledBillIsAcceptedByExistingStrictParser() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.IDENTIFYING,
            "Fatura 08/2099\nVencimento: 15/09/2099\nValor total: R$ 123,45");
        assertEquals(ClaraConversation.Kind.BILL, action.kind);
    }

    @Test public void unrelatedChatWaitsInsteadOfInventingAReply() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.STARTED, "Olá, sou a Clara. Como posso ajudar?");
        assertEquals(ClaraConversation.Kind.WAIT, action.kind);
    }

    @Test public void newestLogicalStepWinsEvenWhenOldPromptRemainsVisible() {
        ClaraConversation.Action action = ClaraConversation.decide(
            ClaraConversation.Stage.IDENTIFYING,
            "Informe a unidade consumidora\n99999999\nAgora informe o CPF do titular");
        assertEquals(ClaraConversation.Kind.MESSAGE, action.kind);
        assertEquals("DOCUMENT", action.value);
    }
}
