package br.com.jarviscerrado.poco;

/**
 * Contrato do login da Agência Web da Equatorial (host {@code go.*}).
 *
 * Este é um portal DIFERENTE do {@code LoginGO.aspx} do host {@code goias.*}.
 * O ASPX é guardado pelo Transmit Security DRS, que recusou a automação em
 * silêncio; aqui não há DRS nenhum — o portão é reCAPTCHA v3 do Google, por
 * pontuação. São gates distintos, e o resultado de um não prediz o do outro.
 *
 * A classe é pura e sem Android de propósito: tudo que ela contém foi lido do
 * DOM e do código-fonte de {@code auth-go.js} em produção, e é justamente a
 * parte que erra silenciosamente se for adivinhada. Quem dirige a página é o
 * motor; quem sabe COMO a página funciona é este arquivo.
 *
 * O que o {@code auth-go.js} faz, na ordem, conferido no fonte:
 *
 * <ol>
 *   <li>intercepta o submit do formulário {@code #login-box-form-go};</li>
 *   <li>chama {@code grecaptcha.execute(siteKey, {action:'login'})} e espera o
 *       token — o token é produzido pela PRÓPRIA página;</li>
 *   <li>monta {@code {documento, uc, service}} a partir do FormData;</li>
 *   <li>faz POST para {@code {site_base_url}/ajax-requests/ajax-auth-go} com os
 *       cabeçalhos {@code X-Recaptcha-Response} e {@code X-Recaptcha-Action};</li>
 *   <li>se 200, grava o JWT em {@code localStorage.jwt} e navega para
 *       {@code /sua-conta/{service}};</li>
 *   <li>se não-200, mostra a mensagem genérica de código {@code #gh678}.</li>
 * </ol>
 *
 * REGRA QUE NÃO NEGOCIA: o ROD aciona o botão visível e deixa o reCAPTCHA da
 * página produzir o token. Nunca fabricar, montar ou repetir token, e nunca
 * chamar o endpoint direto. Se a pontuação recusar, é recusa legítima e o
 * proprietário é avisado — foi o que fizemos com o DRS.
 */
final class AgenciaWebLogin {

    /** Página que hospeda o formulário do titular. */
    static final String LOGIN_URL = "https://go.equatorialenergia.com.br/sua-conta/";

    /** Área logada, para onde o script navega quando não há serviço no caminho. */
    static final String ACCOUNT_URL = "https://go.equatorialenergia.com.br/sua-conta";

    /**
     * Endpoint do login, apenas para RECONHECER a resposta na trilha de rede.
     *
     * Não é para ser chamado. Está aqui para o motor saber qual requisição
     * observar, e porque documentar o alvo proibido é mais seguro do que deixar
     * a próxima pessoa descobri-lo sozinha.
     */
    static final String AUTH_ENDPOINT_PATH = "/ajax-requests/ajax-auth-go";

    static final String FORM = "#login-box-form-go";

    /** CPF/CNPJ do titular. Campo VISÍVEL do formulário. */
    static final String FIELD_DOCUMENT = "#identificador";

    /**
     * Unidade consumidora — e o nome do campo é {@code senha}, não {@code uc}.
     *
     * Custou uma leitura do fonte descobrir. O {@code auth-go.js} monta o JSON
     * com {@code uc: data.get('senha')}, ou seja, a UC entra por
     * {@code #senha-identificador}. Os campos {@code #identificador-conta-contrato}
     * ({@code name=contrato-novo}) e {@code #identificador-2} existem no HTML,
     * estão INVISÍVEIS e NÃO são lidos por este handler: preencher aqueles
     * mandaria a UC vazia com aparência de formulário completo.
     */
    static final String FIELD_UNIT = "#senha-identificador";

    /** Serviço de destino; o script preenche a partir do caminho da URL. */
    static final String FIELD_SERVICE = "#service-login";

    /** Botão de submit do formulário do titular ({@code name=envia-dados}). */
    static final String SUBMIT = "#login-box-form-go button[type=submit]";

    /** Caixa onde o script escreve a falha genérica. */
    static final String ERROR_BOX = "#error-message-login";

    /**
     * Fecha o aviso de LGPD SEM enviar nada.
     *
     * O outro botão do mesmo aviso é {@code #lgpd_accept}, rotulado "Enviar", e
     * ele SUBMETE o formulário de consentimento. Fechar e consentir não são a
     * mesma ação, e o ROD não tem autorização para consentir em nome do
     * proprietário — então o alvo é o "Fechar", nunca o "Enviar".
     */
    static final String LGPD_CLOSE = "button.btn-close.lgpd-btn-close";

    /** Botão de consentimento do aviso de LGPD. Nunca acionar. */
    static final String LGPD_ACCEPT = "#lgpd_accept";

    /** Marcador estrutural de sessão viva: o script guarda o JWT aqui. */
    static final String JWT_KEY = "jwt";

    /** Código da falha genérica do portal, presente na mensagem ao usuário. */
    static final String GENERIC_FAILURE_CODE = "#gh678";

    /** Comprimento da conta contrato aceito pelo portal. */
    static final int UNIT_LENGTH = 15;

    private AgenciaWebLogin() { }

    /** Como o formulário terminou, do ponto de vista de quem olhou a página. */
    enum Outcome {
        /** JWT presente: autenticado. Marcador estrutural, não texto. */
        AUTHENTICATED,
        /**
         * O portal recusou e não disse por quê.
         *
         * Credencial errada e pontuação de reCAPTCHA reprovada produzem
         * EXATAMENTE a mesma mensagem {@code #gh678}, porque o script escreve
         * o mesmo texto para qualquer status diferente de 200. Distinguir os
         * dois exige o status HTTP da resposta, que só a trilha de rede tem.
         */
        REFUSED,
        /** Ainda sem veredito: nem JWT, nem erro, formulário ainda na tela. */
        PENDING,
        /** A página não é a do formulário nem a área logada. */
        UNKNOWN
    }

    /**
     * Traduz a observação da página em desfecho.
     *
     * A ordem importa: JWT vem primeiro porque é estrutural. A caixa de erro
     * fica no DOM sempre, visível ou não, então presença não é veredito —
     * quem decide é estar visível.
     */
    static Outcome classify(boolean jwtPresent, boolean errorVisible, boolean loginFormPresent) {
        if (jwtPresent) return Outcome.AUTHENTICATED;
        if (errorVisible) return Outcome.REFUSED;
        if (loginFormPresent) return Outcome.PENDING;
        return Outcome.UNKNOWN;
    }

    /**
     * Normaliza o documento como o próprio {@code auth-go.js} normaliza.
     *
     * O script remove ponto, hífen, barra e espaço, e passa para maiúsculas —
     * e a linha que removia todo não-dígito está COMENTADA no fonte. A
     * diferença não é cosmética: CNPJ alfanumérico existe desde 2026, e um
     * {@code replaceAll("\\D","")} apagaria as letras e transformaria um
     * documento válido em recusa inexplicável.
     */
    static String document(String raw) {
        if (raw == null) return "";
        return raw.replaceAll("[.\\-/\\s]", "").toUpperCase();
    }

    /**
     * Conta contrato no formato que o portal espera: dígitos à direita,
     * zeros à esquerda até {@link #UNIT_LENGTH}.
     *
     * Uma unidade já mais longa que o limite volta inalterada em vez de ser
     * cortada: truncar identificador é inventar outra conta, e é melhor o
     * portal recusar um valor que veio do cofre do que o ROD consultar a conta
     * de outra pessoa.
     */
    static String unit(String raw) {
        String digits = raw == null ? "" : raw.replaceAll("\\D", "");
        if (digits.isEmpty()) return "";
        if (digits.length() >= UNIT_LENGTH) return digits;
        StringBuilder padded = new StringBuilder(UNIT_LENGTH);
        for (int i = digits.length(); i < UNIT_LENGTH; i++) padded.append('0');
        return padded.append(digits).toString();
    }

    /**
     * O serviço de destino, derivado do caminho como o script deriva.
     *
     * O script usa o segundo segmento do caminho, e o campo oculto decide para
     * onde a navegação vai DEPOIS do login. Carregar {@code /sua-conta/} deixa
     * o serviço vazio e cai na home da área logada; carregar a rota do serviço
     * leva direto a ela, poupando um passo de navegação autenticada.
     */
    static String serviceFor(String path) {
        if (path == null) return "";
        String[] parts = path.split("/");
        int seen = 0;
        for (String part : parts) {
            if (part.isEmpty()) continue;
            seen++;
            if (seen == 2) return part;
        }
        return "";
    }

    // --------------------------------------------- leitura de débitos (go.*)

    /** Rota do funil de cartão, que ANTES de cobrar lista o que está em aberto. */
    static final String DEBTS_PATH = "/pagamento-de-faturas-on-line/";

    /**
     * Palavras que denunciam um controle capaz de INICIAR cobrança.
     *
     * Esta lista é um freio, não um catálogo: qualquer uma delas no rótulo já
     * proíbe o clique. Ela é generosa de propósito — "confirmar" e "finalizar"
     * entram mesmo podendo ser inocentes, porque o custo de não clicar num botão
     * inofensivo é uma medição incompleta, e o custo de clicar num botão de
     * cobrança é dinheiro do proprietário saindo da conta dele.
     */
    static final String[] PAYMENT_WORDS = {
        "pagar", "pague", "pagamento", "cartao", "credito", "debito", "parcel",
        "negoci", "pix", "boleto", "checkout", "finalizar", "confirmar",
        "prosseguir para", "avancar para o pagamento"
    };

    /** Único rótulo que autoriza avanço: o passo que troca UC por lista de débito. */
    static final String[] ADVANCE_WORDS = { "continuar" };

    /**
     * Rótulos de interface que podem ser registrados por extenso.
     *
     * Diagnosticar "qual botão o motor viu" exige mostrar o rótulo, e rótulo de
     * página autenticada não é texto neutro: o cabeçalho desta mesma área começa
     * com uma saudação que traz o nome do titular. Então o log passa por uma
     * lista de permissão — palavra conhecida de interface sai inteira, e
     * qualquer outra coisa sai como tamanho. É o mesmo princípio de
     * {@link RodLog#describe(String)}, aplicado a rótulo em vez de a valor.
     */
    static final String[] PUBLIC_LABEL_WORDS = {
        "continuar", "menu", "sair", "voltar", "fechar", "enviar", "buscar",
        "consultar", "emitir", "detalhes", "baixar", "imprimir", "cookies",
        "aceitar", "rejeitar", "definicoes", "configurar", "pagar", "pagamento",
        "cartao", "credito", "debito", "pix", "boleto", "parcelar", "negociar",
        "finalizar", "confirmar", "selecione", "unidade consumidora"
    };

    /**
     * O rótulo, se for palavra de interface conhecida; senão, só o tamanho.
     *
     * Devolve o rótulo inteiro quando ele CASA a lista, e não a palavra casada,
     * porque para julgar um botão o dono precisa ler "continuar para pagamento"
     * por extenso — é a diferença entre um controle que lista e um que cobra.
     */
    static String publicLabel(String foldedLabel) {
        if (foldedLabel == null || foldedLabel.isEmpty()) return "vazio";
        for (String word : PUBLIC_LABEL_WORDS) {
            if (foldedLabel.contains(word)) {
                return foldedLabel.length() > 60 ? foldedLabel.substring(0, 60) : foldedLabel;
            }
        }
        return "outro(" + foldedLabel.length() + " chars)";
    }

    /**
     * Este controle pode ser acionado sem risco de iniciar cobrança?
     *
     * Exige as duas condições ao mesmo tempo: casar o vocabulário de avanço E
     * não casar nenhuma palavra de pagamento. Um botão "Continuar para
     * pagamento" tem as duas coisas, e é exatamente o caso que a regra precisa
     * recusar — por isso a proibição vence o consentimento, nunca o contrário.
     */
    static boolean safeToAdvance(String foldedLabel) {
        if (foldedLabel == null || foldedLabel.isEmpty()) return false;
        for (String word : PAYMENT_WORDS) if (foldedLabel.contains(word)) return false;
        for (String word : ADVANCE_WORDS) if (foldedLabel.contains(word)) return true;
        return false;
    }

    /**
     * Como o portal diz "não há o que pagar".
     *
     * Sem isto, um funil que volta vazio é indistinguível de um funil quebrado, e
     * a diferença decide o veredito: conta em dia é resposta CORRETA do portal e
     * não defeito do canal. Registrar qual frase apareceu é o que permite dizer
     * "não havia débito hoje" em vez de "a Equatorial não entrega valor".
     */
    static final String[] DEBT_NOTICE_WORDS = {
        // Só a forma mais curta de cada família: "nao ha debitos" nunca casaria,
        // porque "nao ha debito" já é prefixo dele e vence primeiro.
        "nao ha debito", "nenhum debito", "sem debito",
        "nao possui debito", "nenhuma fatura", "nenhuma conta", "em dia",
        "nao foram encontrados", "nao encontramos", "nenhum registro",
        "nada consta", "adimplente", "quitad"
    };

    /** Qual frase de "sem débito" o texto contém. Devolve a palavra, não o texto. */
    static String debtNotice(String foldedText) {
        if (foldedText == null) return "";
        for (String word : DEBT_NOTICE_WORDS) if (foldedText.contains(word)) return word;
        return "";
    }

    /** O que este canal entrega como CONSULTA — que não é a mesma coisa que pagar. */
    enum ReadProvider {
        /** Valor e referência legíveis: dá para poupar o login manual do dono. */
        READ_PROVIDER_OK,
        /** A área respondeu, mas sem valor e referência confiáveis. */
        READ_PROVIDER_UNAVAILABLE
    }

    /**
     * Consulta só vale como consulta com os DOIS campos.
     *
     * Valor sem referência não diz de qual mês é a conta, e referência sem valor
     * não diz quanto pagar. Metade disso mandaria o proprietário conferir no
     * portal assim mesmo, que é justamente o trabalho manual que este canal
     * existe para eliminar.
     */
    static ReadProvider readProvider(boolean amountPresent, boolean referencePresent) {
        return amountPresent && referencePresent
            ? ReadProvider.READ_PROVIDER_OK
            : ReadProvider.READ_PROVIDER_UNAVAILABLE;
    }

    /**
     * O funil realmente saiu da escolha de unidade?
     *
     * Alteração isolada no tamanho do texto não vale: banners de cookies e o
     * reCAPTCHA mudam o DOM enquanto a mesma etapa continua aberta. Só há
     * avanço quando surge conteúdo de fatura/conta em dia ou quando o seletor
     * visível da etapa desaparece.
     */
    static boolean debtStepChanged(int selectsBefore, int selectsNow,
                                   int currencyMarks, int tableRows,
                                   boolean debtNotice) {
        return currencyMarks > 0 || tableRows > 0 || debtNotice
            || selectsNow < selectsBefore;
    }

    // ------------------------------------------------------- ponte entre hosts

    /**
     * Vocabulário dos links de serviço, para achar a Agência Virtual pelo RÓTULO.
     *
     * O portal {@code go.*} é institucional e o {@code goias.*} é o ASPX de
     * autoatendimento; se existir ponte entre os dois, ela aparece como link
     * normal de menu. Procurar por rótulo é o que um usuário faz, e é a única
     * busca que o proprietário autorizou: nada de parâmetro montado à mão nem
     * de endpoint deduzido.
     *
     * As palavras vêm sem acento porque a comparação é feita sobre
     * {@link EquatorialSession#fold(String)}.
     */
    static final String[] SERVICE_WORDS = {
        "agencia virtual", "segunda via", "2a via", "2 via", "duplicata",
        "minhas faturas", "faturas", "fatura", "debito automatico",
        "autoatendimento", "servicos", "sua conta", "historico de consumo"
    };

    /**
     * Palavras que o ASPX escreve quando NÃO há sessão, para nomear a página
     * sem transportar o texto dela.
     *
     * O {@code SegundaVia.aspx} não devolve formulário de login: ele redireciona,
     * e a página de destino é curta. Registrar qual destas palavras apareceu diz
     * ao proprietário POR QUE a área recusou, e é a única forma de dizer isso sem
     * copiar conteúdo de página para dentro do log.
     */
    static final String[] BILL_NOTICE_WORDS = {
        "sessao expirada", "sessao expirou", "faca seu login", "faca login",
        "acesso restrito", "nao autorizado", "indisponivel", "manutencao",
        "erro", "suporte"
    };

    /** Qual palavra do aviso o texto da página contém. Devolve a palavra, não o texto. */
    static String noticeWord(String foldedText) {
        if (foldedText == null) return "";
        for (String word : BILL_NOTICE_WORDS) if (foldedText.contains(word)) return word;
        return "";
    }

    /** Host do autoatendimento ASPX, o outro lado da ponte. */
    static final String BILL_HOST = "goias.equatorialenergia.com.br";

    /**
     * Qual palavra do vocabulário o rótulo do link contém.
     *
     * Devolve a PALAVRA, não o rótulo. O cabeçalho de uma área autenticada
     * costuma trazer o nome do titular no próprio texto do link, e reduzir o
     * rótulo ao termo genérico que ele casou é o que permite registrar a
     * navegação na trilha sem carregar dado do proprietário para o log.
     */
    static String serviceWord(String foldedLabel) {
        if (foldedLabel == null) return "";
        for (String word : SERVICE_WORDS) if (foldedLabel.contains(word)) return word;
        return "";
    }

    /**
     * A rota observada prova que o experimento chegou ao host de faturas?
     *
     * Existe porque "ponte fechada" tem duas causas que o veredito sozinho não
     * separa: a sessão não atravessa, ou a execução nunca bateu na porta. Só a
     * primeira é resposta da concessionária. Chegar ao host e receber a página
     * curta de aviso ({@code Suporte.aspx}) não conta como ter chegado À ÁREA:
     * é o destino que o servidor dá a quem não tem sessão, então aceitá-lo como
     * prova de alcance apagaria justamente a distinção que interessa.
     *
     * Ficou aqui, e não no motor, para que o critério de aceite do experimento
     * seja verificável por teste em vez de conferido a olho num arquivo.
     */
    static boolean reachedBillHost(String destination) {
        if (destination == null) return false;
        String folded = destination.toLowerCase();
        return folded.startsWith(BILL_HOST) && !folded.contains("suporte.aspx");
    }

    /**
     * O que a área de faturas do {@code goias.*} mostra depois da navegação.
     *
     * Os cinco marcadores são estruturais e do próprio ASPX: o formulário de
     * login do cabeçalho, e os quatro controles da segunda via. Basear a
     * conclusão neles é o que separa "a página respondeu" de "a página
     * respondeu AUTENTICADA".
     *
     * O que já foi OBSERVADO sem sessão: o {@code SegundaVia.aspx} não devolve
     * formulário de login — ele redireciona para uma página curta de aviso
     * ({@code Suporte.aspx}, ~100 caracteres). Ela renderiza praticamente vazia,
     * e foi por isso que ela já foi descrita como "em branco" num motor e como
     * "redirecionamento" noutro: é a mesma coisa vista de dois lugares. O que o
     * servidor faz é redirecionar; o que a tela parece é vazio.
     */
    enum BridgeState {
        /** A área de faturas veio autenticada: a ponte existe. */
        OPEN,
        /** A página respondeu, mas sem os controles: sessão não atravessou. */
        CLOSED,
        /**
         * Página em branco: nem controles, nem login, nem texto.
         *
         * NÃO é a assinatura conhecida de "sem sessão" — essa é o
         * redirecionamento para a página de aviso, que tem texto. Este estado
         * existe para o caso de a página não vir de todo: render que falhou,
         * rede que caiu, motor que não pintou. Separá-lo de {@link #CLOSED}
         * evita mandar o dono procurar problema de credencial quando o problema
         * foi não ter chegado resposta nenhuma.
         */
        BLANK,
        /** Não houve login no {@code go.*}, então não há o que medir. */
        NOT_TESTED
    }

    /**
     * Classifica a área de faturas por marcador estrutural, nunca por texto.
     *
     * A exigência de TODOS os quatro controles é deliberada. Um postback parcial
     * do ASPX já devolveu combo sem botão, e aceitar "quase" como ponte aberta
     * faria o ROD prometer uma consulta que não completa.
     */
    static BridgeState bridge(boolean authenticatedOnGo, boolean loginForm, boolean comboUnit,
                              boolean emissionType, boolean reason, boolean emitButton,
                              boolean anyText) {
        if (!authenticatedOnGo) return BridgeState.NOT_TESTED;
        if (comboUnit && emissionType && reason && emitButton && !loginForm) return BridgeState.OPEN;
        if (!anyText && !loginForm && !comboUnit) return BridgeState.BLANK;
        return BridgeState.CLOSED;
    }

    /**
     * Vocabulário do relatório desta rodada, para o desfecho do login {@code go.*}.
     *
     * Dois estados NÃO aparecem aqui de propósito: credencial errada e reprovação
     * de reCAPTCHA. O {@code auth-go.js} escreve a MESMA mensagem {@code #gh678}
     * para qualquer status diferente de 200, então a página não contém a
     * informação que os separaria. Quem lê o DOM só pode dizer "recusado", e
     * dizer mais do que isso seria inventar diagnóstico.
     */
    enum GoOutcome {
        /** JWT presente depois do envio: sessão criada pelo portal. */
        GO_LOGIN_OK,
        /** Recusa opaca: pode ser credencial, pode ser pontuação. */
        GO_LOGIN_REJECTED,
        /** O motor não obteve página nenhuma. */
        GO_PORTAL_ERROR,
        /** O prazo acabou sem veredito observável. */
        GO_TIMEOUT
    }

    /** Traduz o estado da máquina de sessão no vocabulário do relatório. */
    static GoOutcome outcome(EquatorialSession.State state) {
        switch (state) {
            case LOGIN_OK:
            case SESSION_VALID:
                return GoOutcome.GO_LOGIN_OK;
            case LOGIN_REFUSED_OPAQUE:
                return GoOutcome.GO_LOGIN_REJECTED;
            case LOGIN_IN_PROGRESS:
                return GoOutcome.GO_TIMEOUT;
            default:
                return GoOutcome.GO_PORTAL_ERROR;
        }
    }

    /**
     * O formulário está pronto para envio?
     *
     * Enviar com campo vazio gasta uma das duas tentativas do job e volta com a
     * mesma mensagem genérica de credencial errada — indistinguível de recusa
     * real. Conferir antes é o que mantém o diagnóstico honesto.
     */
    static boolean ready(String documentValue, String unitValue) {
        return document(documentValue).length() >= 11
            && unit(unitValue).length() == UNIT_LENGTH;
    }
}
