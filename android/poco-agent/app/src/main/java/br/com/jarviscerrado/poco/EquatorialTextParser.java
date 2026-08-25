package br.com.jarviscerrado.poco;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Leitura determinística da página da Equatorial. Função pura, sem Android.
 *
 * O ROD precisa de cinco dados: valor, vencimento, referência, e código de barras
 * ou PIX. Nada além disso é extraído. O estado da página é classificado antes de
 * tentar qualquer extração, porque uma tela de login e uma fatura vazia exigem
 * respostas diferentes: a primeira pede login manual, a segunda é falha de leitura.
 *
 * A regra que governa as decisões abaixo: na dúvida, não devolver valor. Uma
 * fatura reportada como ilegível custa uma nova consulta; uma linha digitável
 * inventada ou um valor trocado custam dinheiro do proprietário.
 */
final class EquatorialTextParser {

    enum State {
        /** Página autenticada com fatura legível. */
        BILL,
        /** Sessão caiu: o portal devolveu a tela de autenticação. */
        AUTH_REQUIRED,
        /** Desafio antibot de fato apresentado ao usuário. */
        HUMAN_CHECK,
        /** Autenticado, mas sem fatura legível com segurança nesta tela. */
        NO_BILL
    }

    static final class Page {
        final State state;
        final Map<String, String> fields;

        Page(State state, Map<String, String> fields) {
            this.state = state;
            this.fields = Collections.unmodifiableMap(fields);
        }

        String get(String key) {
            String value = fields.get(key);
            return value == null ? "" : value;
        }
    }

    /** Marcadores comprovados da tela de autenticação do portal. */
    private static final String[] LOGIN_MARKERS = {
        "logingo", "txtuc", "txtdocumento",
        "unidade consumidora *", "cpf ou cnpj *"
    };

    /**
     * Bloqueio antibot real, e não o selo passivo do reCAPTCHA.
     *
     * Procurar apenas por "captcha" acusava verificação humana em qualquer página
     * do portal: o rodapé carrega sempre "protegido por reCAPTCHA".
     */
    private static final String[] HUMAN_CHECK_MARKERS = {
        "access denied", "error 15",
        "verifique que voce", "verifique que você",
        "nao sou um robo", "não sou um robô", "i'm not a robot",
        "resolva o desafio", "confirme que voce", "confirme que você",
        "verificacao de seguranca", "verificação de segurança"
    };

    /** Aceita 1.234,56 e também 1234,56: o portal renderiza das duas formas. */
    private static final String MONEY = "(?:[0-9]{1,3}(?:\\.[0-9]{3})+|[0-9]+),[0-9]{2}";

    /** O total costuma vir rotulado; é a única leitura confiável quando há juros na tela. */
    private static final Pattern AMOUNT_LABELED = Pattern.compile(
        "(?i)(?:total a pagar|valor a pagar|valor do documento|valor da fatura|valor total|total)"
            + "\\s*:?\\s*R\\$\\s*(" + MONEY + ")");
    private static final Pattern AMOUNT_ANY = Pattern.compile("(?i)R\\$\\s*(" + MONEY + ")");

    private static final Pattern DUE_DATE =
        Pattern.compile("(?i)(?:vencimento|vence em|venc\\.)\\s*:?\\s*([0-9]{2}/[0-9]{2}/[0-9]{4})");
    private static final Pattern DATE_ANY =
        Pattern.compile("\\b([0-9]{2}/[0-9]{2}/[0-9]{4})\\b");
    private static final Pattern REFERENCE =
        Pattern.compile("(?i)(?:refer.ncia|m.s de refer.ncia|compet.ncia)\\s*:?\\s*([0-9]{2}/[0-9]{4})");

    /** O checkout oficial da Bemobi identifica a cobrança como "Fatura MM/AAAA". */
    private static final Pattern REFERENCE_INVOICE =
        Pattern.compile("(?i)\\bfatura\\s*:?[ \\t]*([0-9]{2}/[0-9]{4})\\b");

    /**
     * Referência com o mês abreviado, como JUL/2026.
     *
     * A listagem de faturas em aberto do portal escreve assim, e o rótulo
     * "Mês/Ano de referência" fica no cabeçalho da tabela, longe do valor. Exigir
     * rótulo adjacente e mês em dígitos fazia a referência da fatura real passar
     * batido, e a fatura inteira ser reportada como inexistente.
     */
    private static final Pattern REFERENCE_MONTH_NAME = Pattern.compile(
        "(?i)\\b(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[./-]\\s*(\\d{4})\\b");

    private static final String[] MONTHS =
        {"JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"};

    /**
     * Linha digitável de concessionária: 44 a 48 dígitos numa única linha.
     *
     * Os separadores aceitos são espaço, tabulação, ponto e hífen — jamais quebra
     * de linha. A árvore de acessibilidade é juntada com "\n", e permitir \s aqui
     * colava números independentes da página (protocolo, medidor, histórico) numa
     * linha digitável que não existe. Entregar isso como pagável seria pior do que
     * não achar nada.
     */
    private static final Pattern DIGITABLE_LINE =
        Pattern.compile("((?:\\d[ \\t.-]*){44,48})");

    /** PIX copia e cola: payload EMV, sempre iniciado por 000201. */
    private static final Pattern PIX =
        Pattern.compile("(000201[0-9A-Za-z*.\\-:/+]{20,})");

    private static final Pattern UNIT =
        Pattern.compile("(?i)(?:unidade consumidora|\\buc\\b)\\s*:?\\s*(\\d{5,})");

    private EquatorialTextParser() { }

    static Page parse(String raw) {
        String text = raw == null ? "" : raw.replace(' ', ' ');
        String lower = text.toLowerCase();

        // A ordem importa: um desafio antibot pode aparecer sobre a tela de login,
        // e nesse caso o operador precisa saber que é o desafio, não a sessão.
        if (containsAny(lower, HUMAN_CHECK_MARKERS)) return new Page(State.HUMAN_CHECK, empty());
        if (containsAny(lower, LOGIN_MARKERS)) return new Page(State.AUTH_REQUIRED, empty());

        Map<String, String> fields = new LinkedHashMap<>();
        fields.put("amount", amount(text));
        fields.put("due_date", dueDate(text));
        fields.put("reference", reference(text));
        fields.put("barcode", digitableLine(text));
        fields.put("pix", capture(text, PIX));
        fields.put("uc", capture(text, UNIT));

        // O valor sozinho nao identifica uma fatura: qualquer tela do portal pode
        // exibir um R$. Exige-se o valor mais uma ancora temporal — vencimento ou
        // referencia. Exigir o vencimento especificamente descartava a listagem de
        // faturas em aberto do proprio portal, que traz mes de referencia e valor
        // mas nao data de vencimento; uma fatura real era reportada como ausente.
        boolean temAncora = !fields.get("due_date").isEmpty() || !fields.get("reference").isEmpty();
        if (fields.get("amount").isEmpty() || !temAncora)
            return new Page(State.NO_BILL, fields);
        return new Page(State.BILL, fields);
    }

    /**
     * Valor da fatura, preferindo sempre o que estiver rotulado.
     *
     * Pegar o primeiro "R$" da tela devolvia o valor dos juros numa página que
     * mostrava juros antes do total. Sem rótulo, só aceitamos quando a tela tem um
     * único valor: havendo vários e nenhum rotulado, a leitura é ambígua e é mais
     * honesto devolver vazio do que escolher um deles.
     */
    private static String amount(String text) {
        String labeled = capture(text, AMOUNT_LABELED);
        if (!labeled.isEmpty()) return labeled;

        Set<String> distinct = new LinkedHashSet<>();
        Matcher matcher = AMOUNT_ANY.matcher(text);
        while (matcher.find()) distinct.add(matcher.group(1).trim());
        return distinct.size() == 1 ? distinct.iterator().next() : "";
    }

    /** Referencia normalizada para MM/AAAA, venha o mes em digitos ou abreviado. */
    private static String reference(String text) {
        String rotulada = capture(text, REFERENCE);
        if (!rotulada.isEmpty()) return rotulada;
        String fatura = capture(text, REFERENCE_INVOICE);
        if (!fatura.isEmpty()) return fatura;
        Matcher matcher = REFERENCE_MONTH_NAME.matcher(text);
        if (!matcher.find()) return "";
        String mes = matcher.group(1).toUpperCase();
        for (int i = 0; i < MONTHS.length; i++)
            if (MONTHS[i].equals(mes)) return String.format("%02d/%s", i + 1, matcher.group(2));
        return "";
    }

    /**
     * A tabela responsiva da Bemobi expõe primeiro todos os cabeçalhos e depois
     * os valores da linha; por isso VENC. e a data não ficam adjacentes na árvore
     * de acessibilidade. Só usamos a data solta quando há cabeçalho de vencimento
     * e exatamente uma data completa na tela — duas datas seriam ambíguas.
     */
    private static String dueDate(String text) {
        String labeled = capture(text, DUE_DATE);
        if (!labeled.isEmpty()) return labeled;
        String lower = text.toLowerCase();
        if (!lower.contains("venc.") && !lower.contains("vencimento")) return "";
        Set<String> distinct = new LinkedHashSet<>();
        Matcher matcher = DATE_ANY.matcher(text);
        while (matcher.find()) distinct.add(matcher.group(1));
        return distinct.size() == 1 ? distinct.iterator().next() : "";
    }

    /** A linha digitável circula com pontos, espaços e hifens; guardamos só os dígitos. */
    private static String digitableLine(String text) {
        Matcher matcher = DIGITABLE_LINE.matcher(text);
        while (matcher.find()) {
            String digits = matcher.group(1).replaceAll("\\D", "");
            if (digits.length() >= 44 && digits.length() <= 48) return digits;
        }
        return "";
    }

    private static Map<String, String> empty() {
        return new LinkedHashMap<>();
    }

    private static boolean containsAny(String lower, String[] markers) {
        for (String marker : markers) if (lower.contains(marker)) return true;
        return false;
    }

    private static String capture(String text, Pattern pattern) {
        Matcher matcher = pattern.matcher(text);
        return matcher.find() ? matcher.group(1).trim() : "";
    }
}
