package br.com.jarviscerrado.poco;

import java.text.Normalizer;
import java.util.Locale;

/** Decisões puras do atendimento oficial da Clara; nenhuma credencial vive aqui. */
final class ClaraConversation {
    enum Stage { OPENING, STARTED, IDENTIFYING, RESULT }
    enum Kind { WAIT, MESSAGE, CLICK, BILL, NO_DEBT, NOT_REGISTERED, FAILED }

    static final class Action {
        final Kind kind;
        final Stage next;
        final String value;
        Action(Kind kind, Stage next, String value) {
            this.kind = kind; this.next = next; this.value = value;
        }
    }

    private ClaraConversation() { }

    static Action decide(Stage stage, String visibleText) {
        String text = fold(visibleText);
        if (has(text, "bem-vindo(a) ao whatsapp", "concordar e continuar",
                "insira seu numero de telefone", "enter your phone number"))
            return new Action(Kind.NOT_REGISTERED, stage, "");
        if (has(text, "nao foi possivel abrir este link", "numero de telefone compartilhado por url e invalido"))
            return new Action(Kind.FAILED, stage, "contato_oficial_indisponivel");
        if (has(text, "nao ha debitos para", "nao ha debitos em aberto para",
                "nenhuma fatura em aberto", "nao foram encontrados debitos",
                "nao identificamos debitos", "tudo em dia"))
            return new Action(Kind.NO_DEBT, Stage.RESULT, "");

        EquatorialTextParser.Page bill = EquatorialTextParser.parse(visibleText);
        if (bill.state == EquatorialTextParser.State.BILL)
            return new Action(Kind.BILL, Stage.RESULT, "");

        if (has(text, "continuar para a conversa", "continue to chat"))
            return new Action(Kind.CLICK, stage, "continuar para a conversa");
        if (stage == Stage.OPENING)
            return new Action(Kind.MESSAGE, Stage.STARTED, "HELLO");

        if (has(text, "aviso de privacidade", "politica de privacidade", "li e aceito",
                "aceita os termos", "concorda com"))
            return new Action(Kind.CLICK, stage, "aceitar");
        if (has(text, "selecione o estado", "qual estado", "escolha sua distribuidora",
                "selecione a distribuidora"))
            return new Action(Kind.CLICK, stage, "goias");
        if (has(text, "segunda via", "consulta de debitos", "consultar debitos",
                "codigo para pagamento") && stage != Stage.IDENTIFYING)
            return new Action(Kind.CLICK, Stage.IDENTIFYING, "consulta de debitos");
        // A árvore contém mensagens anteriores ainda visíveis. Vale a pergunta
        // que aparece por último, inclusive quando o bot volta uma etapa após
        // recarregar ou rejeitar um formato.
        int confirmation = lastIndex(text, "confirma os dados", "deseja continuar",
            "os dados estao corretos", "podemos continuar");
        int birth = lastIndex(text, "data de nascimento", "nascimento do titular");
        int document = lastIndex(text, "cpf ou cnpj", "informe o cpf", "cpf do titular",
            "documento do titular");
        int unit = lastIndex(text, "unidade consumidora", "numero da uc", "informe a uc",
            "codigo unico", "conta contrato");
        int latest = Math.max(Math.max(confirmation, birth), Math.max(document, unit));
        if (latest < 0) return new Action(Kind.WAIT, stage, "");
        if (latest == confirmation)
            return new Action(Kind.MESSAGE, Stage.IDENTIFYING, "YES");
        if (latest == birth)
            return new Action(Kind.MESSAGE, Stage.IDENTIFYING, "BIRTH");
        if (latest == document)
            return new Action(Kind.MESSAGE, Stage.IDENTIFYING, "DOCUMENT");
        if (latest == unit)
            return new Action(Kind.MESSAGE, Stage.IDENTIFYING, "UNIT");
        return new Action(Kind.WAIT, stage, "");
    }

    static String fold(String value) {
        String normalized = Normalizer.normalize(value == null ? "" : value, Normalizer.Form.NFD);
        return normalized.replaceAll("\\p{M}+", "").toLowerCase(Locale.ROOT);
    }

    private static boolean has(String text, String... markers) {
        for (String marker : markers) if (text.contains(marker)) return true;
        return false;
    }

    private static int lastIndex(String text, String... markers) {
        int latest = -1;
        for (String marker : markers) latest = Math.max(latest, text.lastIndexOf(marker));
        return latest;
    }
}
