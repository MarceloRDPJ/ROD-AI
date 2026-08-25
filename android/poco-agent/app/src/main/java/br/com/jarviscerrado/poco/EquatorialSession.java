package br.com.jarviscerrado.poco;

import java.text.Normalizer;

/**
 * Sessão do portal da Equatorial tratada como ESTADO, e não como erro.
 *
 * Antes, expirar era o fim da consulta: o agente devolvia EQUATORIAL_AUTH_REQUIRED
 * e alguém tinha de abrir o Chrome do Poço e entrar à mão. Isso deixava a consulta
 * dependente de uma pessoa acordada. Aqui, a sessão caída é apenas mais um estado
 * do caminho, e cada estado tem uma decisão única e explicável.
 *
 * Duas invariantes de segurança governam a máquina, e são o motivo dela existir
 * como classe pura, testável na JVM, e não como uma sequência de ifs no serviço:
 *
 *  1. No máximo {@link #MAX_LOGIN_ATTEMPTS} tentativas de login por job. Repetir
 *     credencial contra o portal é força bruta contra a conta do proprietário,
 *     ainda que involuntária, e é assim que uma conta legítima é bloqueada.
 *  2. Credencial recusada é TERMINAL. Se o portal disse que a unidade, o documento
 *     ou o titular não conferem, tentar de novo — inclusive por outro motor — é a
 *     mesma força bruta com outra roupa. O erro é tipado e o fluxo para.
 *
 * A classe não conhece Android de propósito: quem observa a tela é o serviço de
 * acessibilidade, quem observa o DOM é o motor WebView, e ambos entregam aqui o
 * mesmo vocabulário.
 */
final class EquatorialSession {

    /** Onde a sessão está, do ponto de vista de quem acabou de olhar a tela. */
    enum State {
        /** Página autenticada e pronta para a consulta. */
        SESSION_VALID,
        /** O portal devolveu a tela de autenticação. Caminho tratado, não falha. */
        SESSION_EXPIRED,
        /** Credenciais enviadas; ainda sem veredito observável. */
        LOGIN_IN_PROGRESS,
        /** Autenticou: a área logada apareceu depois do envio. */
        LOGIN_OK,
        /** O portal afirmou que unidade, documento ou titular não conferem. */
        LOGIN_REJECTED,
        /**
         * O portal recusou e não disse por quê.
         *
         * Estado próprio porque a Agência Web ({@code go.*}) responde a mesma
         * mensagem genérica para credencial errada e para pontuação de reCAPTCHA
         * reprovada — o {@code auth-go.js} escreve o código {@code #gh678} para
         * qualquer status diferente de 200. Chamar isso de credencial recusada
         * mandaria o proprietário conferir o cofre quando o problema pode ser o
         * antifraude, e chamar de verificação humana diria que houve desafio na
         * tela quando não houve. É terminal como os dois, e honesto sobre a
         * dúvida.
         */
        LOGIN_REFUSED_OPAQUE,
        /** Desafio antibot real na frente. Nunca se tenta contornar. */
        HUMAN_CHECK,
        /** O navegador não entregou página nenhuma: aba morta, guia trocada, WebView vazio. */
        BROWSER_STALE
    }

    /** O que fazer a seguir. Uma decisão por observação, sempre a mesma para o mesmo estado. */
    enum Decision {
        /** Segue a consulta: selecionar imóvel, emitir, ler. */
        PROCEED,
        /** Autenticar com as credenciais do cofre. */
        LOGIN,
        /** Aguardar o veredito do envio atual, sem reenviar credenciais. */
        WAIT,
        /** Recarregar a rota autenticada no mesmo lugar. */
        RELOAD_ROUTE,
        /** Encerrar a sessão do portal e reabrir a rota, sem tocar em dados do Chrome. */
        REOPEN_TAB,
        /** Tentar o motor WebView do ROD, com cookie jar próprio. */
        FALLBACK_WEBVIEW,
        /** Terminal: o portal recusou a credencial. */
        FAIL_LOGIN_REJECTED,
        /** Terminal: o portal recusou sem dizer o motivo (credencial ou antifraude). */
        FAIL_REFUSED_OPAQUE,
        /** Terminal: verificação humana em todos os motores. */
        FAIL_HUMAN_CHECK,
        /** Terminal: acabaram as tentativas permitidas. */
        FAIL_EXHAUSTED,
        /** Terminal: o cofre não tem o que é preciso para autenticar. */
        FAIL_NO_CREDENTIALS
    }

    /** Duas, e não mais: a terceira já seria insistência contra a conta do dono. */
    static final int MAX_LOGIN_ATTEMPTS = 2;
    /** Observações transitórias permitidas depois de um único envio. */
    static final int MAX_TRANSIENT_REFILLS = 4;
    /** Recarregar, e depois reabrir. Além disso o problema não é o navegador. */
    static final int MAX_BROWSER_RECOVERIES = 2;

    private final boolean credentialsAvailable;
    private int loginAttempts;
    private int transientRefills;
    private int browserRecoveries;
    private boolean webViewTried;

    EquatorialSession(boolean credentialsAvailable) {
        this.credentialsAvailable = credentialsAvailable;
    }

    int loginAttempts() { return loginAttempts; }
    int transientRefills() { return transientRefills; }
    int browserRecoveries() { return browserRecoveries; }
    boolean webViewTried() { return webViewTried; }

    /** Marca que o motor alternativo já foi usado; sem isso ele seria tentado em laço. */
    void markWebViewTried() { webViewTried = true; }

    /**
     * A decisão para o estado observado, consumindo a tentativa correspondente.
     *
     * Chamar duas vezes com o mesmo estado não devolve a mesma coisa de propósito:
     * é o que garante que a segunda observação de sessão caída leve ao segundo (e
     * último) login, e a terceira ao fim do caminho.
     */
    Decision observe(State state) {
        switch (state) {
            case SESSION_VALID:
            case LOGIN_OK:
                return Decision.PROCEED;
            case LOGIN_REJECTED:
                // Terminal por segurança, não por desistência.
                return Decision.FAIL_LOGIN_REJECTED;
            case LOGIN_REFUSED_OPAQUE:
                // Também terminal: sem saber SE foi a credencial, repetir seria
                // arriscar força bruta contra a conta do dono no escuro.
                return Decision.FAIL_REFUSED_OPAQUE;
            case HUMAN_CHECK:
                if (!webViewTried) return Decision.FALLBACK_WEBVIEW;
                return Decision.FAIL_HUMAN_CHECK;
            case LOGIN_IN_PROGRESS:
                if (!credentialsAvailable) return Decision.FAIL_NO_CREDENTIALS;
                // O portal histórico mantém os campos visíveis enquanto processa
                // o POST. Reenviar aqui duplica a requisição e pode bloquear uma
                // credencial legítima. Portanto cada reaparição apenas consome uma
                // espera limitada; LOGIN só pode nascer de SESSION_EXPIRED.
                if (transientRefills < MAX_TRANSIENT_REFILLS) {
                    transientRefills++;
                    return Decision.WAIT;
                }
                if (!webViewTried) return Decision.FALLBACK_WEBVIEW;
                return Decision.FAIL_EXHAUSTED;
            case SESSION_EXPIRED:
                if (!credentialsAvailable) return Decision.FAIL_NO_CREDENTIALS;
                if (loginAttempts < MAX_LOGIN_ATTEMPTS) {
                    loginAttempts++;
                    return Decision.LOGIN;
                }
                if (!webViewTried) return Decision.FALLBACK_WEBVIEW;
                return Decision.FAIL_EXHAUSTED;
            case BROWSER_STALE:
                if (browserRecoveries < MAX_BROWSER_RECOVERIES) {
                    browserRecoveries++;
                    return browserRecoveries == 1 ? Decision.RELOAD_ROUTE : Decision.REOPEN_TAB;
                }
                if (!webViewTried) return Decision.FALLBACK_WEBVIEW;
                return Decision.FAIL_EXHAUSTED;
            default:
                return Decision.FAIL_EXHAUSTED;
        }
    }

    /** Mensagem tipada de um desfecho terminal, no formato que o Pi já sabe interpretar. */
    static String errorFor(Decision decision) {
        switch (decision) {
            case FAIL_LOGIN_REJECTED:
                return "EQUATORIAL_LOGIN_REJECTED: o portal recusou a unidade consumidora "
                    + "ou o documento cadastrados no cofre";
            case FAIL_REFUSED_OPAQUE:
                return "EQUATORIAL_LOGIN_REFUSED: a Agencia Web recusou o acesso sem dizer o "
                    + "motivo; pode ser a credencial do cofre ou a pontuacao do reCAPTCHA";
            case FAIL_HUMAN_CHECK:
                return "EQUATORIAL_HUMAN_CHECK: a Equatorial pediu verificacao humana nos dois motores";
            case FAIL_NO_CREDENTIALS:
                return "EQUATORIAL_CREDENTIALS_MISSING: o cofre do Poco nao tem unidade "
                    + "consumidora e documento para autenticar sozinho";
            case FAIL_EXHAUSTED:
            default:
                return "EQUATORIAL_LOGIN_FAILED: nao consegui reautenticar no portal "
                    + "dentro do limite de tentativas do job";
        }
    }

    static boolean terminal(Decision decision) {
        return decision == Decision.FAIL_LOGIN_REJECTED
            || decision == Decision.FAIL_REFUSED_OPAQUE
            || decision == Decision.FAIL_HUMAN_CHECK
            || decision == Decision.FAIL_EXHAUSTED
            || decision == Decision.FAIL_NO_CREDENTIALS;
    }

    // ---------------------------------------------------------------- classificação

    /**
     * Recusa explícita do portal. Só é consultada com a tela de login ainda na frente.
     *
     * O texto exato varia com a validação que falhou, então a lista é ampla. Ela
     * não é a única proteção: mesmo que uma recusa nova escape a todos estes
     * marcadores, o limite de duas tentativas encerra o job. A lista serve para
     * dizer ao proprietário QUE a credencial está errada em vez de "não consegui".
     */
    private static final String[] REJECTION_MARKERS = {
        "nao conferem", "nao confere",
        "dados invalidos", "dados incorretos",
        "cpf ou cnpj invalido", "cpf invalido", "cnpj invalido", "documento invalido",
        "unidade consumidora invalida", "unidade consumidora nao encontrada",
        "nao foi possivel localizar", "nao localizamos", "nao encontramos",
        "nao e o titular", "titular divergente",
        "usuario ou senha", "senha invalida"
    };

    /**
     * Marcadores textuais da tela de autenticação.
     *
     * Servem só como reforço: quem decide é o parâmetro estrutural {@code loginFields},
     * porque um rodapé com a palavra "entrar" existe em toda página do portal e o
     * texto sozinho já classificou errado uma vez.
     */
    private static final String[] LOGIN_FIELD_MARKERS = {
        "txtuc", "txtdocumento", "logingo"
    };

    /**
     * Página de erro do portal, e o que ela realmente significa.
     *
     * Descoberta no aparelho: sem sessão válida, a rota da segunda via não devolve
     * a tela de login — ela estoura no servidor e cai em Suporte.aspx, "Sistema
     * Indisponível". Ler isso como navegador em estado ruim gastava as duas
     * recuperações de navegador e nunca chegava ao login, que é exatamente o que
     * resolve. Aqui vale como sessão caída: o caminho de volta é autenticar.
     */
    private static final String[] PORTAL_ERROR_MARKERS = {
        "aspxerrorpath", "sistema indisponivel",
        "nosso sistema encontra-se indisponivel"
    };

    /**
     * Marcador da área autenticada: o portal só oferece "Sair" para quem entrou.
     *
     * A tela de login não tem esse link — conferido no aparelho. Serve para
     * reconhecer que o login deu certo mesmo quando ele desemboca na home, e não
     * na segunda via.
     */
    private static final String[] AUTHENTICATED_MARKERS = { "sair" };

    /**
     * Traduz uma observação de tela no estado da sessão.
     *
     * A ordem é deliberada. Desafio antibot vem primeiro, porque ele pode estar
     * sobre a tela de login e nesse caso o problema não é a credencial. Página
     * autenticada vem antes de qualquer suspeita de login, porque o marcador
     * estrutural (o seletor de unidade existe) vale mais do que qualquer texto:
     * o rodapé do portal carrega palavras de login em toda página.
     *
     * @param pageText          texto da tela, como a árvore ou o DOM entregou
     * @param browserResponding o navegador entregou alguma página
     * @param billSelector      o seletor de unidade da segunda via está presente
     * @param loginFields       os dois campos de autenticação do portal estão presentes
     * @param afterSubmit       a observação é posterior a um envio de credenciais
     */
    static State classify(String pageText, boolean browserResponding,
                          boolean billSelector, boolean loginFields, boolean afterSubmit) {
        if (!browserResponding) return State.BROWSER_STALE;
        String folded = fold(pageText);
        EquatorialTextParser.Page page = EquatorialTextParser.parse(pageText);

        if (page.state == EquatorialTextParser.State.HUMAN_CHECK) return State.HUMAN_CHECK;
        if (billSelector) return afterSubmit ? State.LOGIN_OK : State.SESSION_VALID;

        // Antes de julgar a tela de login: o erro do portal aparece COM o cabeçalho
        // da área logada, e decidir por ele daria autenticado a quem não está.
        if (containsAny(folded, PORTAL_ERROR_MARKERS))
            return afterSubmit ? State.LOGIN_IN_PROGRESS : State.SESSION_EXPIRED;

        boolean loginScreen = loginFields
            || page.state == EquatorialTextParser.State.AUTH_REQUIRED
            || containsAny(folded, LOGIN_FIELD_MARKERS);
        if (loginScreen) {
            if (afterSubmit && containsAny(folded, REJECTION_MARKERS)) return State.LOGIN_REJECTED;
            return afterSubmit ? State.LOGIN_IN_PROGRESS : State.SESSION_EXPIRED;
        }
        if (page.state == EquatorialTextParser.State.BILL
            || containsAny(folded, AUTHENTICATED_MARKERS)) {
            // Dentro do portal, ainda que não na segunda via: quem chama leva a
            // navegação até a rota certa.
            return afterSubmit ? State.LOGIN_OK : State.SESSION_VALID;
        }
        // Página que não é login, não é fatura e não tem o seletor: navegador em
        // estado ruim (aba errada, erro de rede, página em branco).
        return State.BROWSER_STALE;
    }

    /**
     * Estado da sessão na Agência Web ({@code go.*}), por marcador ESTRUTURAL.
     *
     * O portal ASPX obriga a ler texto de tela, e texto engana: o rodapé carrega
     * palavras de login em toda página, e foi assim que a classificação errou uma
     * vez. Aqui existe algo melhor: o {@code auth-go.js} só grava
     * {@code localStorage.jwt} depois de um 200 do servidor. Presença do JWT é,
     * portanto, prova de sessão viva — não indício.
     *
     * O que este método NÃO faz: distinguir credencial errada de reCAPTCHA
     * reprovado. A página não tem essa informação, e inventá-la aqui seria pior
     * do que admitir a dúvida. Quem quiser o status HTTP tem de olhar a rede.
     *
     * @param jwtPresent       {@code localStorage.jwt} existe e não está vazio
     * @param errorVisible     a caixa de erro do formulário está visível
     * @param loginFormPresent o formulário do titular está no DOM
     * @param browserResponding o motor entregou alguma página
     * @param afterSubmit      a observação é posterior a um envio
     */
    static State classifyAgenciaWeb(boolean jwtPresent, boolean errorVisible,
                                    boolean loginFormPresent, boolean browserResponding,
                                    boolean afterSubmit) {
        if (!browserResponding) return State.BROWSER_STALE;
        if (jwtPresent) return afterSubmit ? State.LOGIN_OK : State.SESSION_VALID;
        // Só depois do envio um erro visível é veredito. Antes dele, a caixa
        // pode ter sobrado de uma tentativa anterior na mesma aba.
        if (errorVisible && afterSubmit) return State.LOGIN_REFUSED_OPAQUE;
        if (loginFormPresent) return afterSubmit ? State.LOGIN_IN_PROGRESS : State.SESSION_EXPIRED;
        return State.BROWSER_STALE;
    }

    /**
     * Minúsculas sem acento.
     *
     * O portal escreve "inválidos" e a árvore de acessibilidade às vezes devolve
     * o mesmo texto sem acento, dependendo da fonte do nó. Comparar as duas formas
     * duplicaria cada marcador.
     */
    static String fold(String value) {
        if (value == null) return "";
        String normalized = Normalizer.normalize(value, Normalizer.Form.NFD);
        return normalized.replaceAll("\\p{M}+", "").toLowerCase();
    }

    private static boolean containsAny(String folded, String[] markers) {
        for (String marker : markers) if (folded.contains(marker)) return true;
        return false;
    }

    /**
     * Duas escritas da mesma unidade consumidora.
     *
     * O portal grava a UC com zeros à esquerda até quinze dígitos; o cofre guarda
     * os onze que o proprietário conhece. Comparar as strings cruas recusava a
     * fatura certa do imóvel certo.
     */
    static boolean sameUnit(String shown, String expected) {
        return !strip(expected).isEmpty() && strip(shown).equals(strip(expected));
    }

    private static String strip(String value) {
        if (value == null) return "";
        String digits = value.replaceAll("\\D", "");
        return digits.replaceFirst("^0+(?!$)", "");
    }
}
