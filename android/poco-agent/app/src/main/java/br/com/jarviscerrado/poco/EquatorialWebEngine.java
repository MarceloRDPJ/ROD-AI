package br.com.jarviscerrado.poco;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.Locale;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Segundo motor de leitura: WebView do próprio ROD, com sessão só dele.
 *
 * O motor principal usa o Chrome do aparelho, e por isso não pode limpar nada:
 * apagar cookies lá derrubaria a sessão de todos os outros sites do proprietário.
 * Aqui a situação é oposta. Este WebView tem cookie jar próprio, cache próprio e
 * nenhum outro site dentro, então limpar é a recuperação correta e não custa nada
 * a ninguém: a limpeza feita antes de cada autenticação alcança exclusivamente o
 * que este arquivo criou.
 *
 * Ele existe como alternativa, não como substituto: entra quando o Chrome fica em
 * estado ruim ou quando a reautenticação por acessibilidade não fecha. Vantagem
 * real: DOM em vez de árvore de acessibilidade, o que elimina gesto, diálogo
 * nativo e elemento abaixo da dobra. Desvantagem real: o portal vê um user agent
 * de WebView, e um WAF pode responder com desafio — nesse caso o resultado é o
 * erro tipado de verificação humana, e nunca uma tentativa de contornar.
 *
 * Nada de conteúdo de página entra em log: só passo, presença e contagem.
 */
final class EquatorialWebEngine {

    static final String LOGIN_URL = "https://goias.equatorialenergia.com.br/LoginGO.aspx";
    static final String BILL_URL =
        "https://goias.equatorialenergia.com.br/AgenciaGO/Servi%C3%A7os/aberto/SegundaVia.aspx";
    /** Sem janela real: o WebView precisa de um tamanho para dispor o layout. */
    private static final int VIEWPORT_WIDTH = 1080;
    private static final int VIEWPORT_HEIGHT = 1920;
    /** Prazo de um evaluateJavascript. Se estourar, o motor está travado, não lento. */
    private static final long EVAL_TIMEOUT_MILLIS = 8_000L;
    /** Prazo para uma navegação assentar em readyState complete. */
    private static final long LOAD_TIMEOUT_MILLIS = 30_000L;
    private static final long POLL_MILLIS = 400L;
    /** Prazo do veredito do login: o portal gera o token de risco antes de enviar. */
    private static final long LOGIN_WAIT_MILLIS = 30_000L;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicBoolean failed = new AtomicBoolean(false);
    /** Quantas paginas terminaram de carregar. E o que distingue "outra pagina" de "a mesma". */
    private final AtomicInteger loads = new AtomicInteger();
    private WebView webView;

    private EquatorialWebEngine() { }

    /**
     * Consulta completa pelo motor alternativo: autentica, escolhe o imóvel, emite e lê.
     *
     * @param unit     unidade consumidora esperada (dígitos, como o cofre guarda)
     * @param document CPF ou CNPJ do titular
     * @param deadline instante em que o job desiste; o motor respeita o que sobrou
     */
    static JSONObject read(Context context, String unit, String document, long deadline)
            throws Exception {
        EquatorialWebEngine engine = new EquatorialWebEngine();
        try {
            return engine.run(context.getApplicationContext(), unit, document, deadline);
        } finally {
            engine.destroy();
        }
    }

    /**
     * Entrega do QR do PIX. O base64 nunca passa por log, nem truncado.
     *
     * Callback e nao retorno porque quem chama e a ponte, que roda na thread
     * principal, e este motor precisa dela livre para o WebView responder.
     */
    interface PixSink {
        void onPix(String base64);
        void onFailure(String error);
    }

    /**
     * QR do PIX da fatura pedida, em base64, lido do DOM em vez de da tela.
     *
     * Conferido contra a pagina real: em SegundaViaDownload.aspx cada linha tem um
     * controle com onclick="pix(this,'iVBORw0KGgo...')", e o argumento e o QR ja em
     * PNG. Ler dali dispensa screenshot, resolucao, contraste e adivinhar qual
     * celula e a do PIX pela posicao — as duas incertezas do caminho por imagem.
     *
     * A linha e escolhida pela REFERENCIA pedida, nunca pela posicao, e a escolha e
     * do {@link PixRowLocator}, que ja trata AGO/2026 e 08/2026 como a mesma coisa.
     * Ambiguidade e ausencia falham: entregar o QR da fatura errada custa dinheiro
     * do proprietario.
     *
     * Validacao do payload nao acontece aqui — quem valida e {@link PixPayload},
     * depois de o QR ser decodificado. Este metodo entrega imagem, nao payload.
     */
    static void pixBase64ForReference(Context context, String unit, String document,
                                      String reference, long timeoutMillis, PixSink sink) {
        final Context app = context.getApplicationContext();
        final long deadline = System.currentTimeMillis() + timeoutMillis;
        new Thread(new Runnable() {
            @Override public void run() {
                EquatorialWebEngine engine = new EquatorialWebEngine();
                try {
                    sink.onPix(engine.runPix(app, unit, document, reference, deadline));
                } catch (Exception error) {
                    String message = error.getMessage();
                    sink.onFailure(message == null || message.isEmpty()
                        ? error.getClass().getSimpleName() : message);
                } finally {
                    engine.destroy();
                }
            }
        }, "rod-pix-webview").start();
    }

    private String runPix(Context context, String unit, String document,
                          String reference, long deadline) throws Exception {
        create(context);
        RodLog.step("webview", "motor alternativo iniciado para o QR de pix");
        reachBillPage(unit, document, deadline);
        emitFullInvoice(deadline);
        return pixFromDownloadPage(reference);
    }

    /** Extrai os pares (texto da linha, base64 do QR) e escolhe pela referencia. */
    private String pixFromDownloadPage(String reference) throws Exception {
        String script =
            "(function(){"
            + "var out=[];"
            + "var nodes=document.querySelectorAll('[onclick]');"
            + "for(var i=0;i<nodes.length;i++){"
            + "var oc=nodes[i].getAttribute('onclick')||'';"
            + "if(oc.toLowerCase().indexOf('pix')<0) continue;"
            + "var m=oc.match(/'([A-Za-z0-9+/=]{40,})'/);"
            + "if(!m) continue;"
            + "var row=nodes[i].closest('tr');"
            + "out.push({text:row?row.innerText:'',b64:m[1]});}"
            + "return JSON.stringify({rows:out});"
            + "})()";
        JSONObject dom = new JSONObject(evalJson(script));
        JSONArray rows = dom.optJSONArray("rows");
        int total = rows == null ? 0 : rows.length();
        RodLog.step("webview", "linhas com controle de pix=" + total);
        if (total == 0) throw new IllegalStateException(
            "EQUATORIAL_PAYMENT_DATA_NOT_FOUND: a pagina de download nao expos QR de pix");

        List<String> texts = new ArrayList<>();
        for (int i = 0; i < total; i++) texts.add(rows.getJSONObject(i).optString("text", ""));
        int index = PixRowLocator.invoiceIndex(texts, reference);
        RodLog.step("webview", "linha escolhida pela referencia=" + (index >= 0));
        if (index == PixRowLocator.AMBIGUOUS) throw new IllegalStateException(
            "EQUATORIAL_PAYMENT_DATA_NOT_FOUND: mais de uma fatura casa com a referencia pedida");
        if (index < 0 || index >= total) throw new IllegalStateException(
            "EQUATORIAL_PAYMENT_DATA_NOT_FOUND: nenhuma fatura com a referencia pedida nesta tela");

        String base64 = rows.getJSONObject(index).optString("b64", "");
        // Presenca e tamanho, nunca o conteudo: este e o QR de pagamento do dono.
        RodLog.step("webview", "qr de pix obtido=" + !base64.isEmpty()
            + " tamanho=" + base64.length());
        if (base64.isEmpty()) throw new IllegalStateException(
            "EQUATORIAL_PAYMENT_DATA_NOT_FOUND: controle de pix sem imagem embutida");
        return base64;
    }

    // ------------------------------------------------------------------ fluxo

    private JSONObject run(Context context, String unit, String document, long deadline)
            throws Exception {
        create(context);
        RodLog.step("webview", "motor alternativo iniciado");

        reachBillPage(unit, document, deadline);
        selectUnit(unit, deadline);
        emit(deadline);
        return readBill(unit);
    }

    /**
     * Deixa a segunda via autenticada na frente, autenticando se for preciso.
     *
     * Sessao caida e pagina de erro do portal levam ao mesmo lugar: a tela de
     * acesso. Verificacao humana e credencial recusada param aqui, e param de vez.
     */
    private void reachBillPage(String unit, String document, long deadline) throws Exception {
        load(BILL_URL, deadline);
        Observation seen = observe(false);
        RodLog.step("webview", "estado inicial=" + seen.state);

        if (seen.state == EquatorialSession.State.SESSION_EXPIRED
            || seen.state == EquatorialSession.State.BROWSER_STALE) {
            seen = authenticate(unit, document, deadline);
            RodLog.step("webview", "estado apos autenticar=" + seen.state);
        }
        if (seen.state == EquatorialSession.State.HUMAN_CHECK)
            throw new IllegalStateException(
                "EQUATORIAL_HUMAN_CHECK: a Equatorial pediu verificacao humana no motor WebView");
        if (seen.state == EquatorialSession.State.LOGIN_REJECTED)
            throw new IllegalStateException(
                EquatorialSession.errorFor(EquatorialSession.Decision.FAIL_LOGIN_REJECTED));
        if (seen.state != EquatorialSession.State.SESSION_VALID
            && seen.state != EquatorialSession.State.LOGIN_OK)
            throw new IllegalStateException(
                EquatorialSession.errorFor(EquatorialSession.Decision.FAIL_EXHAUSTED));

        if (!seen.billSelector) {
            load(BILL_URL, deadline);
            seen = observe(false);
            if (!seen.billSelector) throw new IllegalStateException(
                "EQUATORIAL_BILL_NOT_FOUND: o motor WebView nao carregou o seletor de unidade");
        }
    }

    /**
     * Autentica no portal por DOM.
     *
     * Escrever num campo dispara JS que limpa o outro: o mesmo comportamento que
     * obrigou o motor de acessibilidade a conferir e reaplicar. Aqui a conferência
     * é imediata, porque ler o value de volta custa uma chamada.
     */
    private Observation authenticate(String unit, String document, long deadline) throws Exception {
        // Cookie velho é a causa mais comum de o portal insistir na tela de login.
        // Neste motor, apagá-lo é seguro.
        dropOwnSessionInline();
        load(LOGIN_URL, deadline);

        String script =
            "(function(){"
            + "var u=document.getElementById('WEBDOOR_headercorporativogo_txtUC');"
            + "var d=document.getElementById('WEBDOOR_headercorporativogo_txtDocumento');"
            + "if(!u||!d) return JSON.stringify({fields:false});"
            + "function put(el,v){el.focus();el.value=v;"
            + "el.dispatchEvent(new Event('input',{bubbles:true}));"
            + "el.dispatchEvent(new Event('change',{bubbles:true}));}"
            + "put(u," + JSONObject.quote(unit) + ");"
            + "put(d," + JSONObject.quote(document) + ");"
            + "put(u," + JSONObject.quote(unit) + ");"
            + "return JSON.stringify({fields:true,unit:u.value.length>0,doc:d.value.length>0});"
            + "})()";
        JSONObject filled = new JSONObject(evalJson(script));
        RodLog.step("webview", "campos de login presentes=" + filled.optBoolean("fields", false)
            + " unidade=" + filled.optBoolean("unit", false)
            + " documento=" + filled.optBoolean("doc", false));
        if (!filled.optBoolean("unit", false) || !filled.optBoolean("doc", false))
            throw new IllegalStateException(
                "EQUATORIAL_PORTAL_TIMEOUT: o motor WebView nao encontrou a tela de autenticacao");

        // Só o botão que uma pessoa consegue tocar. O <input> de postback do
        // ASP.NET existe escondido na página, e aciona-lo direto pularia o token
        // de risco que o portal exige — isso seria contornar um controle de
        // seguranca, e nao e o que este motor faz.
        String submit =
            "(function(){"
            + "var b=Array.prototype.slice.call(document.querySelectorAll("
            + "'button,input[type=submit],input[type=button],a')).filter(function(e){"
            + "var t=((e.value||'')+' '+(e.textContent||'')).trim().toLowerCase();"
            + "return t==='entrar'&&!!e.offsetParent;});"
            + "var risk=(typeof window.tsPlatform!=='undefined');"
            + "if(!b.length) return JSON.stringify({clicked:false,risk:risk});"
            + "b[0].click();"
            + "return JSON.stringify({clicked:true,risk:risk});"
            + "})()";
        JSONObject sent = new JSONObject(evalJson(submit));
        boolean clicked = sent.optBoolean("clicked", false);
        // O portal gera um token de risco pelo SDK dele antes de enviar o
        // formulario. Se esse SDK nao carregou no WebView, o login nao acontece —
        // e saber disso pela trilha evita procurar o defeito no lugar errado.
        RodLog.step("webview", "ENTRAR acionado=" + clicked
            + " sdk de risco do portal presente=" + sent.optBoolean("risk", false));
        if (!clicked) throw new IllegalStateException(
            "EQUATORIAL_PORTAL_TIMEOUT: botao ENTRAR ausente no motor WebView");

        // Agir devolve sucesso sem a página ter reagido, aqui como no Chrome: o que
        // vale é a próxima observação. E o veredito demora, porque o portal só faz
        // o postback depois de o token existir — observar uma vez e concluir dava
        // login falhado um instante antes de ele dar certo.
        return awaitLoginOutcome(Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS));
    }

    /** Observa até o portal decidir, ou até o prazo. Estado, nunca tempo fixo. */
    private Observation awaitLoginOutcome(long limit) throws Exception {
        Observation seen = observe(true);
        while (seen.state == EquatorialSession.State.LOGIN_IN_PROGRESS
            && System.currentTimeMillis() < limit) {
            Thread.sleep(POLL_MILLIS * 2);
            seen = observe(true);
        }
        return seen;
    }

    // ------------------------------------------------ Agência Web (host go.*)

    /**
     * Autentica na Agência Web, o portal do host {@code go.*}.
     *
     * É outro portal, não outra rota: o ASPX do host {@code goias.*} é guardado
     * pelo Transmit Security DRS, que recusou a automação em silêncio, e aqui o
     * portão é reCAPTCHA v3. O que este método faz é preencher o formulário e
     * acionar o botão visível — o token de reCAPTCHA é produzido pela PRÓPRIA
     * página, no {@code submit} que o {@code auth-go.js} intercepta. Nada de
     * token fabricado, repetido ou montado à mão, e nenhuma chamada direta ao
     * endpoint de autenticação. Se a pontuação recusar, é recusa legítima.
     *
     * Devolve o estado observado; não navega para a consulta. Quem decide o
     * próximo passo é {@link EquatorialSession}.
     */
    static JSONObject loginAgenciaWeb(Context context, String unit, String document, long deadline)
            throws Exception {
        EquatorialWebEngine engine = new EquatorialWebEngine();
        try {
            return engine.runAgenciaWeb(context.getApplicationContext(), unit, document, deadline);
        } finally {
            engine.destroy();
        }
    }

    private JSONObject runAgenciaWeb(Context context, String unit, String document, long deadline)
            throws Exception {
        create(context);
        load(AgenciaWebLogin.LOGIN_URL, deadline);

        JSONObject seen = observeAgenciaWeb();
        EquatorialSession.State state = EquatorialSession.classifyAgenciaWeb(
            seen.optBoolean("jwt", false), seen.optBoolean("err", false),
            seen.optBoolean("form", false), respondingAgenciaWeb(seen), false);
        RodLog.step("agenciaweb", "estado inicial=" + state);
        // Sessão já viva: não há motivo para enviar credencial de novo, e cada
        // envio evitado é uma tentativa que não se gasta contra a conta do dono.
        if (state == EquatorialSession.State.SESSION_VALID)
            return outcomeAgenciaWeb(state, true);

        String doc = AgenciaWebLogin.document(document);
        String uc = AgenciaWebLogin.unit(unit);
        RodLog.step("agenciaweb", "credenciais do cofre documento="
            + RodLog.describe(doc) + " unidade=" + RodLog.describe(uc));
        if (!AgenciaWebLogin.ready(doc, uc))
            throw new IllegalStateException(EquatorialSession.errorFor(
                EquatorialSession.Decision.FAIL_NO_CREDENTIALS));

        closeLgpdNotice();
        fillAgenciaWeb(doc, uc);
        submitAgenciaWeb();
        return awaitAgenciaWebOutcome(
            Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS));
    }

    /**
     * Observa a página por marcador estrutural.
     *
     * O JWT sai como BOOLEANO. Ele é credencial portadora: quem tem o token
     * entra na conta, então ele não atravessa esta fronteira nem para virar
     * tamanho em log.
     */
    private JSONObject observeAgenciaWeb() throws Exception {
        String script =
            "(function(){"
            + "var j=null;try{j=localStorage.getItem('jwt');}catch(e){}"
            + "var f=document.querySelector('" + AgenciaWebLogin.FORM + "');"
            + "var eb=document.querySelector('" + AgenciaWebLogin.ERROR_BOX + "');"
            + "var vis=false;"
            + "if(eb){var cs=getComputedStyle(eb);"
            + "vis=(cs.display!=='none'&&cs.visibility!=='hidden'"
            + "&&(eb.innerText||'').trim().length>0);}"
            + "return JSON.stringify({jwt:!!(j&&j.length>0),form:!!f,err:vis,"
            + "len:(document.body?document.body.innerText.length:0)});"
            + "})()";
        return new JSONObject(evalJson(script));
    }

    private boolean respondingAgenciaWeb(JSONObject seen) {
        return !failed.get() && (seen.optBoolean("form", false)
            || seen.optBoolean("jwt", false) || seen.optInt("len", 0) > 0);
    }

    /**
     * Fecha o aviso de LGPD sem consentir com ele.
     *
     * O aviso cobre o formulário, então sair dele é pré-requisito. O outro botão
     * do mesmo aviso é {@code #lgpd_accept}, rotulado "Enviar", e ele SUBMETE o
     * consentimento — o ROD não tem autorização para consentir em nome do
     * proprietário, então o alvo é só o "Fechar". Ausente conta como fechado:
     * o aviso não aparece em toda visita.
     */
    private void closeLgpdNotice() throws Exception {
        String script =
            "(function(){"
            + "var b=document.querySelector('" + AgenciaWebLogin.LGPD_CLOSE + "');"
            + "if(!b) return JSON.stringify({present:false,closed:true});"
            + "var r=b.getBoundingClientRect();"
            + "if(r.width===0||r.height===0) return JSON.stringify({present:false,closed:true});"
            + "b.click();"
            + "var a=document.querySelector('" + AgenciaWebLogin.LGPD_CLOSE + "');"
            + "var ar=a?a.getBoundingClientRect():null;"
            + "return JSON.stringify({present:true,closed:(!ar||ar.width===0||ar.height===0)});"
            + "})()";
        JSONObject shut = new JSONObject(evalJson(script));
        RodLog.step("agenciaweb", "aviso lgpd presente=" + shut.optBoolean("present", false)
            + " fechado=" + shut.optBoolean("closed", false));
    }

    /**
     * Preenche os dois campos que o handler realmente lê.
     *
     * A UC vai em {@code #senha-identificador} ({@code name=senha}), porque é
     * dali que o {@code auth-go.js} tira o campo {@code uc} do JSON. Os campos
     * {@code #identificador-conta-contrato} e {@code #identificador-2} existem no
     * HTML, estão invisíveis e não são lidos: preenchê-los mandaria a UC vazia
     * com aparência de formulário completo.
     *
     * Escreve, reobserva e reescreve — a mesma lição do outro portal: a máscara
     * de entrada reformata o campo depois do primeiro evento, e conferir só o
     * que foi escrito daria preenchido a um campo que a máscara esvaziou.
     */
    private void fillAgenciaWeb(String document, String unit) throws Exception {
        String script =
            "(function(){"
            + "var d=document.querySelector('" + AgenciaWebLogin.FIELD_DOCUMENT + "');"
            + "var u=document.querySelector('" + AgenciaWebLogin.FIELD_UNIT + "');"
            + "if(!d||!u) return JSON.stringify({fields:false});"
            + "function put(el,v){el.focus();el.value=v;"
            + "el.dispatchEvent(new Event('input',{bubbles:true}));"
            + "el.dispatchEvent(new Event('change',{bubbles:true}));}"
            + "put(d," + JSONObject.quote(document) + ");"
            + "put(u," + JSONObject.quote(unit) + ");"
            + "put(d," + JSONObject.quote(document) + ");"
            + "put(u," + JSONObject.quote(unit) + ");"
            + "var s=document.querySelector('" + AgenciaWebLogin.FIELD_SERVICE + "');"
            + "return JSON.stringify({fields:true,"
            + "doc:d.value.replace(/\\D/g,'').length,uc:u.value.replace(/\\D/g,'').length,"
            + "service:(s?(s.value||''):'')});"
            + "})()";
        JSONObject filled = new JSONObject(evalJson(script));
        // Contagem de dígitos, nunca o valor: é CPF e unidade consumidora.
        RodLog.step("agenciaweb", "campos presentes=" + filled.optBoolean("fields", false)
            + " digitos_documento=" + filled.optInt("doc", 0)
            + " digitos_unidade=" + filled.optInt("uc", 0)
            + " servico=" + RodLog.sanitize(filled.optString("service", "")));
        if (!filled.optBoolean("fields", false))
            throw new IllegalStateException(
                "EQUATORIAL_PORTAL_TIMEOUT: o formulario da Agencia Web nao apareceu no motor WebView");
        if (filled.optInt("doc", 0) == 0 || filled.optInt("uc", 0) == 0)
            throw new IllegalStateException(
                "EQUATORIAL_PORTAL_TIMEOUT: os campos da Agencia Web nao aceitaram o preenchimento");
    }

    /**
     * Aciona o botão visível de acessar, e nada além disso.
     *
     * O clique dispara o {@code submit} do formulário, e é o próprio
     * {@code auth-go.js} que chama {@code grecaptcha.execute} e monta a
     * requisição. Registrar se o {@code grecaptcha} carregou é o que separa
     * "antifraude recusou" de "o reCAPTCHA nem existia neste motor" — sem isso o
     * defeito seria procurado no lugar errado, como já aconteceu com o SDK de
     * risco do portal ASPX.
     */
    private void submitAgenciaWeb() throws Exception {
        String script =
            "(function(){"
            + "var f=document.querySelector('" + AgenciaWebLogin.FORM + "');"
            + "var g=(typeof grecaptcha!=='undefined'&&!!grecaptcha.execute);"
            + "if(!f) return JSON.stringify({clicked:false,recaptcha:g});"
            + "var b=f.querySelector('button[type=submit]');"
            + "if(!b||!b.offsetParent) return JSON.stringify({clicked:false,recaptcha:g});"
            + "b.click();"
            + "return JSON.stringify({clicked:true,recaptcha:g});"
            + "})()";
        JSONObject sent = new JSONObject(evalJson(script));
        RodLog.step("agenciaweb", "acessar acionado=" + sent.optBoolean("clicked", false)
            + " recaptcha carregado=" + sent.optBoolean("recaptcha", false));
        if (!sent.optBoolean("clicked", false))
            throw new IllegalStateException(
                "EQUATORIAL_PORTAL_TIMEOUT: botao Acessar ausente no motor WebView");
    }

    /**
     * Espera o veredito por estado, com prazo.
     *
     * O caminho é assíncrono de ponta a ponta — o token do reCAPTCHA é uma
     * promessa e o login é um {@code fetch} — então observar uma vez logo depois
     * do clique daria "sem veredito" um instante antes de o veredito existir.
     */
    private JSONObject awaitAgenciaWebOutcome(long limit) throws Exception {
        EquatorialSession.State state = EquatorialSession.State.LOGIN_IN_PROGRESS;
        boolean jwt = false;
        while (System.currentTimeMillis() < limit) {
            JSONObject seen = observeAgenciaWeb();
            jwt = seen.optBoolean("jwt", false);
            state = EquatorialSession.classifyAgenciaWeb(jwt, seen.optBoolean("err", false),
                seen.optBoolean("form", false), respondingAgenciaWeb(seen), true);
            if (state != EquatorialSession.State.LOGIN_IN_PROGRESS) break;
            Thread.sleep(POLL_MILLIS * 2);
        }
        RodLog.step("agenciaweb", "estado apos envio=" + state);
        return outcomeAgenciaWeb(state, jwt);
    }

    private JSONObject outcomeAgenciaWeb(EquatorialSession.State state, boolean jwt)
            throws Exception {
        return new JSONObject().put("state", state.name()).put("jwt", jwt);
    }

    // --------------------------------------- leitura de débitos pelo canal go.*

    /**
     * Tenta usar a Agência Web como PROVEDOR DE CONSULTA, e nada mais que isso.
     *
     * A ponte para o autoatendimento não existe (medido nesta mesma rodada), mas
     * o {@code go.*} tem um funil de cartão que, ANTES de cobrar, lista o que está
     * em aberto. Listar débito é leitura, e leitura é o que pouparia o
     * proprietário de logar à mão no ASPX toda vez só para saber o valor.
     *
     * O limite é absoluto e está no código, não na boa intenção: o único controle
     * que este método aciona é o que {@link AgenciaWebLogin#safeToAdvance} aprova,
     * e essa função recusa qualquer rótulo com palavra de pagamento — inclusive
     * "continuar para pagamento". Chegando à tela do débito, o motor PARA. Não
     * existe segundo clique, e a tela seguinte é apenas descrita.
     *
     * Consulta não é obtenção de documento: o resultado separa valor e referência
     * (o produto) da presença de código de barras e PIX (o artefato de pagamento),
     * porque prometer o segundo tendo só o primeiro seria vender o que não temos.
     */
    static JSONObject readDebtsAgenciaWeb(Context context, String unit, String document,
                                          long deadline) throws Exception {
        EquatorialWebEngine engine = new EquatorialWebEngine();
        try {
            return engine.runDebts(context.getApplicationContext(), unit, document, deadline);
        } finally {
            engine.destroy();
        }
    }

    private JSONObject runDebts(Context context, String unit, String document, long deadline)
            throws Exception {
        create(context);
        EquatorialSession.State state = ensureAgenciaWebSession(unit, document, deadline);
        AgenciaWebLogin.GoOutcome outcome = AgenciaWebLogin.outcome(state);
        RodLog.step("consulta", "sessao go.*=" + outcome);
        JSONObject result = new JSONObject()
            .put("go_outcome", outcome.name())
            .put("read_provider", AgenciaWebLogin.ReadProvider.READ_PROVIDER_UNAVAILABLE.name());
        if (outcome != AgenciaWebLogin.GoOutcome.GO_LOGIN_OK) return result;

        // Navegação de usuário: a área logada primeiro, e de lá o link visível do
        // funil. Carregar a rota direto daria o mesmo endereço, mas não provaria
        // que o portal OFERECE o caminho — e é isso que autoriza o ROD a usá-lo.
        load(AgenciaWebLogin.ACCOUNT_URL, deadline);
        JSONObject link = linkWithPath(AgenciaWebLogin.DEBTS_PATH);
        result.put("official_link", link.optBoolean("found", false));
        if (!link.optBoolean("found", false)) {
            RodLog.fail("consulta", "a area logada nao oferece o link do funil");
            return result;
        }
        load(link.optString("href", ""), deadline);
        JSONObject where = whereAmI();
        RodLog.step("consulta", "funil em " + where.optString("host", "")
            + where.optString("path", "") + " caracteres=" + where.optInt("chars", 0));
        result.put("funnel_host", where.optString("host", ""))
              .put("funnel_path", where.optString("path", ""));

        // O aviso de cookies cobre o rodapé do funil e intercepta clique. Fechar
        // é permitido; "aceitar todos" seria consentir em nome do proprietário,
        // e essa não é uma decisão que o ROD tem autorização para tomar.
        closeLgpdNotice();

        JSONObject picked = selectDebtUnit(unit);
        result.put("unit_options", picked.optInt("options", 0))
              .put("unit_matches", picked.optInt("matches", 0))
              .put("unit_selected", picked.optBoolean("selected", false));
        RodLog.step("consulta", "unidades no combo=" + picked.optInt("options", 0)
            + " casaram=" + picked.optInt("matches", 0)
            + " selecionada=" + picked.optBoolean("selected", false));
        if (!picked.optBoolean("selected", false)) return result;

        installNetworkWatch();
        // Um POST de formulário recarrega o documento e leva a escuta de rede
        // junto. Contar os fins de carregamento é o que distingue "o botão não
        // fez nada" de "o portal respondeu com a mesma tela" — duas conclusões
        // opostas que o DOM sozinho apresenta igual.
        int loadsBeforeAdvance = loads.get();
        JSONObject advanced = advanceFromUnitStep();
        result.put("advance_clicked", advanced.optBoolean("clicked", false))
              .put("advance_blocked", advanced.optString("blocked", ""));
        RodLog.step("consulta", "continuar acionado=" + advanced.optBoolean("clicked", false)
            + " freio segurou=" + RodLog.sanitize(advanced.optString("blocked", "")));
        if (!advanced.optBoolean("clicked", false)) return result;

        // O passo é AJAX: o portal troca o miolo da página sem navegar. Esperar
        // por carregamento novo devolveria na hora, com a tela velha, e a
        // primeira medição concluiu "sem débito" justamente assim. Quem manda
        // aqui é a MUDANÇA do conteúdo, com prazo.
        JSONObject changed = awaitDebtContent(
            Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS),
            picked.optInt("visible_selects", 1));
        // Um submit que não vira requisição significa que o próprio script da
        // página interceptou e desistiu. Duas causas conhecidas neste portal:
        // validação recusando o formulário, e o token de reCAPTCHA que o login
        // também espera. Perguntar qual foi é o que separa "a Equatorial recusou"
        // de "acionamos o botão de um jeito que o script não aceita".
        JSONObject why = new JSONObject(evalJson(
            "(function(){"
            + "var vis=[],all=document.querySelectorAll("
            + "'[role=alert],[class*=erro],[class*=error],[class*=alert],[class*=invalid]');"
            + "for(var i=0;i<all.length;i++){var e=all[i];if(!e.offsetParent) continue;"
            + "var t=(e.innerText||'').trim();if(t) vis.push(t);}"
            + "var f=document.querySelector('form');"
            + "return JSON.stringify({alerts:vis.length,"
            + "alert_text:vis.join(' ').normalize('NFD')"
            + ".replace(/[\\u0300-\\u036f]/g,'').toLowerCase(),"
            + "recaptcha:(typeof grecaptcha!=='undefined'&&!!grecaptcha.execute),"
            + "form_action:(f?(f.getAttribute('action')||''):'')});})()"));
        String alertWord = AgenciaWebLogin.noticeWord(why.optString("alert_text", ""));
        if (alertWord.isEmpty())
            alertWord = AgenciaWebLogin.debtNotice(why.optString("alert_text", ""));
        result.put("alerts_visible", why.optInt("alerts", 0))
              .put("alert_word", alertWord)
              .put("recaptcha_on_page", why.optBoolean("recaptcha", false));
        RodLog.step("consulta", "alertas visiveis=" + why.optInt("alerts", 0)
            + " palavra=" + (alertWord.isEmpty() ? "nenhuma" : alertWord)
            + " recaptcha na pagina=" + why.optBoolean("recaptcha", false)
            + " acao do formulario=" + RodLog.sanitize(why.optString("form_action", "")));

        String network = networkWatch();
        int reloads = loads.get() - loadsBeforeAdvance;
        result.put("network", network).put("reloads_after_advance", reloads)
              .put("advance_tag", advanced.optString("tag", ""));

        // Âncora que não navegou: o motor não abre janela nova, então o clique
        // morre em silêncio. Seguir o href que a própria âncora carrega é a mesma
        // navegação que o dedo do proprietário faria — nada é montado aqui.
        // "checkout" fica de fora: é a fronteira do pagamento, e a ordem é parar
        // na tela que LISTA o débito.
        String href = advanced.optString("href", "");
        String path = advanced.optString("path", "");
        if (reloads == 0 && "a".equals(advanced.optString("tag", ""))
            && !href.isEmpty() && !path.toLowerCase().contains("checkout")) {
            RodLog.step("consulta", "a ancora nao navegou sozinha; seguindo o href dela");
            load(href, deadline);
            result.put("followed_anchor", true);
            changed = awaitDebtContent(
                Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS),
                changed.optInt("selects", 0));
            JSONObject landed = whereAmI();
            result.put("anchor_host", landed.optString("host", ""))
                  .put("anchor_path", landed.optString("path", ""));
            RodLog.step("consulta", "a ancora levou a " + landed.optString("host", "")
                + landed.optString("path", ""));
        }
        result.put("chars_before_advance", where.optInt("chars", 0))
              .put("chars_after_advance", changed.optInt("chars", 0))
              .put("currency_marks", changed.optInt("currency", 0))
              .put("table_rows", changed.optInt("rows", 0));
        RodLog.step("consulta", "recarregamentos apos continuar=" + reloads);
        RodLog.step("consulta", "conteudo apos continuar: caracteres="
            + changed.optInt("chars", 0) + " marcas de moeda=" + changed.optInt("currency", 0)
            + " linhas de tabela=" + changed.optInt("rows", 0));
        RodLog.step("consulta", "rede da pagina: " + RodLog.sanitize(network));
        return readDebtScreen(result, unit);
    }

    /**
     * Garante sessão no {@code go.*} sem gastar credencial à toa.
     *
     * O JWT sobrevive entre execuções, e reenviar documento e unidade com sessão
     * viva gastaria uma tentativa contra a conta do proprietário para chegar ao
     * estado em que já estávamos.
     */
    private EquatorialSession.State ensureAgenciaWebSession(String unit, String document,
                                                            long deadline) throws Exception {
        load(AgenciaWebLogin.LOGIN_URL, deadline);
        JSONObject seen = observeAgenciaWeb();
        if (seen.optBoolean("jwt", false)) return EquatorialSession.State.SESSION_VALID;

        String doc = AgenciaWebLogin.document(document);
        String uc = AgenciaWebLogin.unit(unit);
        if (!AgenciaWebLogin.ready(doc, uc))
            throw new IllegalStateException(EquatorialSession.errorFor(
                EquatorialSession.Decision.FAIL_NO_CREDENTIALS));
        closeLgpdNotice();
        fillAgenciaWeb(doc, uc);
        submitAgenciaWeb();
        JSONObject login = awaitAgenciaWebOutcome(
            Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS));
        return EquatorialSession.State.valueOf(login.optString("state",
            EquatorialSession.State.BROWSER_STALE.name()));
    }

    /**
     * Instala uma escuta de rede DENTRO do nosso próprio WebView, antes do clique.
     *
     * Necessária porque "a tela não mudou" tem duas causas opostas e o DOM não
     * as separa: ou o clique não disparou requisição nenhuma — defeito nosso, de
     * como acionamos o controle — ou disparou e o portal recusou — e aí o limite
     * é da concessionária. Reportar a segunda tendo a primeira seria acusar a
     * Equatorial de um erro nosso, que é exatamente o que o proprietário proibiu.
     *
     * O que ela guarda: CAMINHO e status. Nunca corpo, nunca cabeçalho, nunca
     * query string — é ali que moram token e identificador. E ela só OBSERVA o
     * que a página faz por conta própria: nada aqui emite requisição.
     */
    private void installNetworkWatch() throws Exception {
        evalJson(
            "(function(){if(window.__rodWatch) return 'ja';window.__rodWatch=[];"
            + "function note(u,s){try{var p=new URL(u,location.href).pathname;"
            + "window.__rodWatch.push(p+' '+s);}catch(e){}}"
            + "var of=window.fetch;"
            + "if(of) window.fetch=function(){var a=arguments;"
            + "var u=(a[0]&&a[0].url)?a[0].url:String(a[0]);"
            + "return of.apply(this,a).then(function(r){note(u,r.status);return r;},"
            + "function(e){note(u,'falhou');throw e;});};"
            + "var oo=XMLHttpRequest.prototype.open;"
            + "XMLHttpRequest.prototype.open=function(m,u){this.__rodUrl=u;"
            + "this.addEventListener('loadend',function(){note(this.__rodUrl,this.status);});"
            + "return oo.apply(this,arguments);};"
            + "return 'ok';})()");
    }

    /** O que a página pediu por conta própria: caminho e status, nada mais. */
    private String networkWatch() throws Exception {
        return evalJson(
            "(function(){var w=window.__rodWatch||[];"
            + "return w.length?w.slice(-8).join(' ; '):'nenhuma requisicao';})()");
    }

    /**
     * Espera o miolo da página mudar depois do "Continuar", ou o prazo acabar.
     *
     * Três sinais, porque um só engana: o tamanho do texto, a quantidade de
     * marcas de moeda e o número de linhas de tabela. Um funil que listou débito
     * mexe em pelo menos um deles; um funil que não fez nada não mexe em nenhum.
     */
    private JSONObject awaitDebtContent(long limit, int selectsBefore) throws Exception {
        String script =
            "(function(){var t=document.body?document.body.innerText:'';"
            + "var m=t.match(/R\\$/g);"
            + "var s=document.querySelectorAll('select'),vs=0;"
            + "for(var i=0;i<s.length;i++)if(s[i].offsetParent)vs++;"
            + "var f=t.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'')"
            + ".toLowerCase();"
            + "var notice=/(nao ha debito|nenhum debito|sem debito|nao possui debito|"
            + "nenhuma fatura|nenhuma conta|em dia|nao foram encontrados|"
            + "nao encontramos|nenhum registro|nada consta|adimplente|quitad)/.test(f);"
            + "return JSON.stringify({chars:t.trim().length,currency:(m?m.length:0),"
            + "rows:document.querySelectorAll('table tr').length,selects:vs,notice:notice});})()";
        JSONObject seen = new JSONObject(evalJson(script));
        while (System.currentTimeMillis() < limit
            && !AgenciaWebLogin.debtStepChanged(selectsBefore,
                seen.optInt("selects", 0), seen.optInt("currency", 0),
                seen.optInt("rows", 0), seen.optBoolean("notice", false))) {
            Thread.sleep(POLL_MILLIS * 2);
            seen = new JSONObject(evalJson(script));
        }
        return seen;
    }

    /** Acha o link VISÍVEL de um caminho conhecido e devolve o href que ele já tem. */
    private JSONObject linkWithPath(String path) throws Exception {
        String script =
            "(function(){var all=document.querySelectorAll('a[href]');"
            + "for(var i=0;i<all.length;i++){var a=all[i];"
            + "if(!a.offsetParent) continue;"
            + "var r=a.getBoundingClientRect(); if(r.width===0||r.height===0) continue;"
            + "if((a.pathname||'')===" + JSONObject.quote(path) + ")"
            + "return JSON.stringify({found:true,href:a.href});}"
            + "return JSON.stringify({found:false});})()";
        return new JSONObject(evalJson(script));
    }

    /**
     * Escolhe a unidade do cofre no combo do funil, casando por DÍGITOS.
     *
     * Conta quantas opções casaram, e não só se alguma casou: duas casando
     * significaria que o rótulo não identifica a unidade sozinho, e escolher a
     * primeira mostraria ao proprietário a conta de outro imóvel dele como se
     * fosse a pedida. Nenhum dígito sai daqui — só contagem.
     */
    private JSONObject selectDebtUnit(String unit) throws Exception {
        String script =
            "(function(){"
            + "var sels=document.querySelectorAll('select');"
            + "function norm(v){v=(v||'').replace(/\\D/g,'');return v.replace(/^0+(?!$)/,'');}"
            + "var want=norm(" + JSONObject.quote(unit == null ? "" : unit) + ");"
            + "if(!want) return JSON.stringify({options:0,matches:0,selected:false});"
            + "var visible=0;for(var s=0;s<sels.length;s++){var el=sels[s];"
            + "if(!el.offsetParent) continue;"
            + "visible++;"
            + "var hits=[];"
            + "for(var i=0;i<el.options.length;i++){"
            + "if(norm(el.options[i].text)===want||norm(el.options[i].value)===want) hits.push(i);}"
            + "if(hits.length===1){el.selectedIndex=hits[0];"
            + "el.dispatchEvent(new Event('change',{bubbles:true}));"
            + "return JSON.stringify({options:el.options.length,matches:1,selected:true,visible_selects:visible});}"
            + "if(hits.length>1)"
            + "return JSON.stringify({options:el.options.length,matches:hits.length,selected:false});}"
            + "var first=sels.length?sels[0].options.length:0;"
            + "return JSON.stringify({options:first,matches:0,selected:false,visible_selects:visible});})()";
        return new JSONObject(evalJson(script));
    }

    /**
     * Aciona SOMENTE o passo que troca unidade por lista de débito.
     *
     * Cada candidato visível é julgado pelo rótulo em {@link AgenciaWebLogin},
     * fora do JavaScript, para que a regra que protege o dinheiro do
     * proprietário fique num arquivo puro, coberto por teste, e não numa string
     * de script. O que o freio segurou é registrado: saber qual botão foi
     * recusado é o que permite ao dono conferir a decisão em vez de confiar nela.
     */
    private JSONObject advanceFromUnitStep() throws Exception {
        String script =
            "(function(){"
            + "var out=[];"
            + "var all=document.querySelectorAll("
            + "'button,input[type=submit],input[type=button],a.btn');"
            + "for(var i=0;i<all.length;i++){var b=all[i];"
            + "if(!b.offsetParent) continue;"
            + "var r=b.getBoundingClientRect(); if(r.width===0||r.height===0) continue;"
            + "var t=(b.innerText||b.value||'').trim();"
            + "out.push({i:i,label:t.normalize('NFD')"
            + ".replace(/[\\u0300-\\u036f]/g,'').toLowerCase()});}"
            + "return JSON.stringify(out.slice(0,30));})()";
        JSONArray candidates = new JSONArray(evalJson(script));
        int chosen = -1;
        String chosenLabel = "";
        StringBuilder blocked = new StringBuilder();
        StringBuilder inventory = new StringBuilder();
        for (int i = 0; i < candidates.length(); i++) {
            JSONObject candidate = candidates.getJSONObject(i);
            String label = candidate.optString("label", "");
            if (inventory.length() > 0) inventory.append(" | ");
            inventory.append(AgenciaWebLogin.publicLabel(label));
            if (AgenciaWebLogin.safeToAdvance(label)) {
                if (chosen < 0) {
                    chosen = candidate.optInt("i", -1);
                    chosenLabel = AgenciaWebLogin.publicLabel(label);
                }
            } else if (!label.isEmpty()) {
                if (blocked.length() > 0) blocked.append(" | ");
                blocked.append(AgenciaWebLogin.publicLabel(label));
            }
        }
        // O inventário é o que permite ao dono conferir a escolha em vez de
        // confiar nela: mostra tudo que estava clicável e qual foi acionado.
        RodLog.step("consulta", "controles visiveis: " + inventory);
        RodLog.step("consulta", "acionado: " + (chosen < 0 ? "nenhum" : chosenLabel));
        if (chosen < 0)
            return new JSONObject().put("clicked", false).put("blocked", blocked.toString());
        // Descrever o controle ANTES de acioná-lo: âncora com target novo não
        // navega neste motor, que não abre janela, e sem essa informação o
        // silêncio depois do clique pareceria recusa do portal.
        String click =
            "(function(){var all=document.querySelectorAll("
            + "'button,input[type=submit],input[type=button],a.btn');"
            + "var b=all[" + chosen + "];if(!b) return JSON.stringify({clicked:false});"
            + "var tag=(b.tagName||'').toLowerCase();"
            + "var href=(tag==='a'&&b.href)?b.href:'';"
            + "var path='';try{path=href?new URL(href).pathname:'';}catch(e){}"
            + "var form=b.form?((b.form.getAttribute('action')||'')):'';"
            + "b.click();"
            + "return JSON.stringify({clicked:true,tag:tag,target:(b.target||''),"
            + "href:href,path:path,type:(b.type||''),in_form:!!b.form,form_action:form});})()";
        JSONObject sent = new JSONObject(evalJson(click));
        RodLog.step("consulta", "controle acionado: tag=" + sent.optString("tag", "")
            + " type=" + RodLog.sanitize(sent.optString("type", ""))
            + " target=" + RodLog.sanitize(sent.optString("target", ""))
            + " em formulario=" + sent.optBoolean("in_form", false)
            + " destino=" + RodLog.sanitize(sent.optString("path", "")));
        return new JSONObject().put("clicked", sent.optBoolean("clicked", false))
            .put("blocked", blocked.toString())
            .put("tag", sent.optString("tag", ""))
            .put("target", sent.optString("target", ""))
            .put("href", sent.optString("href", ""))
            .put("path", sent.optString("path", ""));
    }

    /**
     * Lê a tela do débito e separa CONSULTA de ARTEFATO DE PAGAMENTO.
     *
     * Valor e referência atravessam a fronteira porque são o produto pedido. O
     * código de barras e o PIX saem como BOOLEANO, porque são instrumentos de
     * pagamento e não têm por que existir em log. A unidade mostrada é conferida
     * contra a do cofre e descartada: confirmar que a tela é do imóvel certo não
     * exige transportar o número.
     *
     * Quem julga o texto é o {@link EquatorialTextParser}, o mesmo dos outros dois
     * motores, para que esta rota não ganhe regra própria — e ele está congelado,
     * então este método o CHAMA e não o corrige.
     */
    private JSONObject readDebtScreen(JSONObject result, String vaultUnit) throws Exception {
        JSONObject markers = new JSONObject(evalJson(
            "(function(){"
            + "function has(re){var n=document.querySelectorAll('input,select');"
            + "for(var i=0;i<n.length;i++){var k=((n[i].name||'')+' '+(n[i].id||'')+' '"
            + "+(n[i].placeholder||'')+' '+(n[i].autocomplete||'')).toLowerCase();"
            + "if(re.test(k)) return true;}return false;}"
            + "var t=document.body?document.body.innerText:'';"
            + "return JSON.stringify({card:has(/cartao|cart\\u00e3o|cc-|card|cvv|cvc|validade|bandeira/),"
            + "chars:t.trim().length,host:location.hostname,path:location.pathname});})()"));
        result.put("card_fields_present", markers.optBoolean("card", false))
              .put("screen_chars", markers.optInt("chars", 0))
              .put("screen_host", markers.optString("host", ""))
              .put("screen_path", markers.optString("path", ""));

        String text = evalJson("document.body?document.body.innerText:''");
        // "Conta em dia" é resposta certa, não falha. Sem procurar por ela, um
        // proprietário sem débito receberia "o canal não funciona" — e mandaria
        // consertar o que já estava certo.
        String debtNotice = AgenciaWebLogin.debtNotice(EquatorialSession.fold(text));
        result.put("debt_notice", debtNotice);
        RodLog.step("consulta", "aviso de debito=" + (debtNotice.isEmpty() ? "nenhum" : debtNotice));
        EquatorialTextParser.Page page = EquatorialTextParser.parse(text);
        boolean amount = !page.get("amount").isEmpty();
        boolean reference = !page.get("reference").isEmpty();
        AgenciaWebLogin.ReadProvider verdict = AgenciaWebLogin.readProvider(amount, reference);

        String shown = page.get("uc").replaceAll("\\D", "");
        String expected = vaultUnit == null ? "" : vaultUnit.replaceAll("\\D", "");
        boolean sameUnit = !shown.isEmpty() && !expected.isEmpty()
            && EquatorialSession.sameUnit(shown, expected);

        RodLog.step("consulta", "tela em " + markers.optString("path", "")
            + " caracteres=" + markers.optInt("chars", 0)
            + " campos de cartao=" + markers.optBoolean("card", false));
        RodLog.step("consulta", "parser=" + page.state
            + " unidade confere=" + sameUnit);
        RodLog.found("consulta", "valor", amount);
        RodLog.found("consulta", "referencia", reference);
        RodLog.found("consulta", "vencimento", !page.get("due_date").isEmpty());
        RodLog.found("consulta", "codigo de barras", !page.get("barcode").isEmpty());
        RodLog.found("consulta", "pix", !page.get("pix").isEmpty());
        RodLog.step("consulta", "veredito=" + verdict);

        return result
            .put("parser_state", page.state.name())
            .put("amount", page.get("amount"))
            .put("reference", page.get("reference"))
            .put("due_date", page.get("due_date"))
            .put("barcode_present", !page.get("barcode").isEmpty())
            .put("pix_present", !page.get("pix").isEmpty())
            .put("shown_unit_matches_vault", sameUnit)
            .put("read_provider", verdict.name());
    }

    // ------------------------------------------ trilha durável do experimento

    /**
     * Arquivo da rodada. Existe porque o logcat já apagou a prova três vezes.
     *
     * A conclusão do experimento decide o rumo do produto, e conclusão sem
     * observação recuperável é opinião com data. O buffer do logcat é circular e
     * rotativo; um arquivo não é. Fica no armazenamento privado do app e é
     * puxado de lá para {@code .tools/} depois da execução.
     */
    private File evidence;
    /**
     * De instância, e não estático: {@link SimpleDateFormat} não é seguro entre
     * threads, e dois motores rodando ao mesmo tempo — a ponte e a consulta —
     * corromperiam o carimbo um do outro em silêncio.
     */
    private final SimpleDateFormat stamp =
        new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);

    /**
     * Abre a trilha da rodada, sem derrubar o experimento se não der.
     *
     * Falha em abrir arquivo NÃO aborta a medição: perder o registro é ruim,
     * perder a medição é pior, e a decisão de qual custo aceitar é melhor tomada
     * aqui do que por uma exceção que sobe no meio da navegação.
     */
    private void openEvidence(Context context, String run) {
        try {
            File dir = new File(context.getFilesDir(), "rod-evidencia");
            if (!dir.exists() && !dir.mkdirs()) return;
            evidence = new File(dir, run + ".jsonl");
        } catch (Throwable error) {
            evidence = null;
            RodLog.fail("prova", "nao consegui abrir o arquivo da trilha");
        }
    }

    /**
     * Grava um passo observado, em uma linha por passo.
     *
     * O que entra aqui é o que já passou pelos filtros do motor: host, caminho,
     * booleanos, contagens e palavra de vocabulário. Nunca query string, nunca
     * rótulo cru, nunca cookie, JWT, documento ou unidade — a trilha é feita
     * para ser lida por outra pessoa, e por isso vale a mesma regra do log.
     */
    private void record(String step, JSONObject data) {
        if (evidence == null) return;
        OutputStreamWriter writer = null;
        try {
            JSONObject line = new JSONObject();
            line.put("t", stamp.format(new Date()));
            line.put("passo", step);
            if (data != null) {
                java.util.Iterator<String> keys = data.keys();
                while (keys.hasNext()) {
                    String key = keys.next();
                    line.put(key, scrubbed(data.get(key)));
                }
            }
            writer = new OutputStreamWriter(new FileOutputStream(evidence, true), "UTF-8");
            writer.write(line.toString());
            writer.write("\n");
        } catch (Throwable error) {
            RodLog.fail("prova", "nao consegui gravar o passo " + step);
        } finally {
            if (writer != null) try { writer.close(); } catch (Throwable ignored) { }
        }
    }

    /**
     * Segunda linha de defesa da trilha: mascara texto, preserva número.
     *
     * A primeira linha é não escrever valor sensível, e o motor já a respeita —
     * o que chega aqui é host, caminho, booleano, contagem e palavra de
     * vocabulário. Mas este arquivo vai para caminho RASTREADO, e dado
     * publicado não volta atrás: se algum dia um campo novo trouxer documento
     * ou unidade por engano, o padrão do {@link RodLog} pega antes do disco.
     *
     * Só texto passa pelo filtro. Número e booleano seguem intactos de
     * propósito: {@code caracteres=12345} é contagem, não identificador, e
     * mascarar contagem destruiria a própria medição que a trilha existe para
     * guardar.
     */
    private Object scrubbed(Object value) throws Exception {
        if (value instanceof String) return RodLog.sanitize((String) value);
        if (value instanceof JSONObject) {
            JSONObject source = (JSONObject) value;
            JSONObject clean = new JSONObject();
            java.util.Iterator<String> keys = source.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                clean.put(key, scrubbed(source.get(key)));
            }
            return clean;
        }
        if (value instanceof JSONArray) {
            JSONArray source = (JSONArray) value;
            JSONArray clean = new JSONArray();
            for (int i = 0; i < source.length(); i++) clean.put(scrubbed(source.get(i)));
            return clean;
        }
        return value;
    }

    /**
     * O host das faturas responde a este motor? Medido, e não assumido.
     *
     * O {@code SegundaVia.aspx} redireciona para uma página curta de aviso, então
     * ele sozinho não distingue "a sessão não vale aqui" de "o motor nem chegou
     * ao host". O {@code LoginGO.aspx} distingue: é uma página do MESMO host que
     * renderiza conteúdo de verdade, e o formulário de login dela é justamente o
     * marcador que uma sessão válida faria desaparecer.
     *
     * É o controle positivo do experimento: enquanto as duas rotas do mesmo host
     * derem respostas DIFERENTES, está provado que a sonda alcança o host e
     * enxerga o que há nele — e aí "antes igual a depois" passa a significar "a
     * sessão não atravessou", que é a conclusão, em vez de "a medição falhou".
     */
    private JSONObject probeBillHostLogin(long deadline) throws Exception {
        load(LOGIN_URL, deadline);
        return probeStructure();
    }

    // ------------------------------------------- ponte go.* -> goias.* (medida)

    /**
     * Mede se o login do {@code go.*} abre a área de faturas do {@code goias.*}.
     *
     * A pergunta parece de arquitetura e é experimental: os dois hosts pertencem
     * à mesma concessionária, mas têm portões diferentes — reCAPTCHA v3 num,
     * Transmit Security DRS no outro — e nada garante que a sessão de um valha
     * no outro. Já erramos por inferência aqui; então o método faz o controle:
     * observa o {@code goias.*} ANTES, autentica no {@code go.*}, navega SÓ por
     * link visível do próprio portal, e observa o {@code goias.*} DEPOIS. Sem o
     * "antes", uma sessão que já existia passaria por resultado do login.
     *
     * O cookie jar é o do WebView do ROD, o mesmo objeto entre as duas metades da
     * medição — e é por isso que a comparação vale: se o marcador estrutural
     * mudar, quem mudou foi o servidor, não o navegador.
     *
     * O que este método NÃO faz, por falta de autorização: montar URL de SSO,
     * copiar token para outra origem, chamar endpoint interno. A navegação é a de
     * um usuário — {@code href} de âncora visível — e se o portal não oferecer
     * link nenhum, o resultado é ponte indisponível, que também é resposta.
     */
    static JSONObject bridgeAgenciaWeb(Context context, String unit, String document, long deadline)
            throws Exception {
        EquatorialWebEngine engine = new EquatorialWebEngine();
        try {
            return engine.runBridge(context.getApplicationContext(), unit, document, deadline);
        } finally {
            engine.destroy();
        }
    }

    private JSONObject runBridge(Context context, String unit, String document, long deadline)
            throws Exception {
        create(context);
        openEvidence(context, "ponte-" + System.currentTimeMillis());

        JSONObject before = probeBillArea(deadline);
        RodLog.step("ponte", "antes do login: " + describeBillArea(before));
        record("antes/segunda-via", before);

        // Controle positivo, na mesma execução e no MESMO host: uma rota que
        // renderiza de verdade. Enquanto ela responder diferente da rota de
        // fatura, está provado que a sonda bate na porta e enxerga o que abre —
        // e só então "antes igual a depois" pode significar "a sessão não
        // atravessou" em vez de "o experimento não chegou lá".
        JSONObject beforeHost = probeBillHostLogin(deadline);
        RodLog.step("ponte", "antes, controle no host de faturas: "
            + describeBillArea(beforeHost));
        record("antes/logingo", beforeHost);

        load(AgenciaWebLogin.LOGIN_URL, deadline);
        JSONObject seen = observeAgenciaWeb();
        boolean already = seen.optBoolean("jwt", false);
        RodLog.step("ponte", "sessao go.* previa=" + already
            + " formulario=" + seen.optBoolean("form", false));

        EquatorialSession.State state;
        if (already) {
            state = EquatorialSession.State.SESSION_VALID;
        } else {
            String doc = AgenciaWebLogin.document(document);
            String uc = AgenciaWebLogin.unit(unit);
            RodLog.step("ponte", "credenciais do cofre documento=" + RodLog.describe(doc)
                + " unidade=" + RodLog.describe(uc));
            if (!AgenciaWebLogin.ready(doc, uc))
                throw new IllegalStateException(EquatorialSession.errorFor(
                    EquatorialSession.Decision.FAIL_NO_CREDENTIALS));
            closeLgpdNotice();
            fillAgenciaWeb(doc, uc);
            submitAgenciaWeb();
            JSONObject login = awaitAgenciaWebOutcome(
                Math.min(deadline, System.currentTimeMillis() + LOGIN_WAIT_MILLIS));
            state = EquatorialSession.State.valueOf(login.optString("state",
                EquatorialSession.State.BROWSER_STALE.name()));
        }

        AgenciaWebLogin.GoOutcome outcome = AgenciaWebLogin.outcome(state);
        boolean authenticated = outcome == AgenciaWebLogin.GoOutcome.GO_LOGIN_OK;
        RodLog.step("ponte", "desfecho do login go.*=" + outcome);
        record("login", new JSONObject()
            .put("desfecho", outcome.name())
            .put("estado", state.name())
            .put("sessao_ja_estava_aberta", already));

        // O JWT aparece ANTES de a navegação do script terminar: o auth-go.js grava
        // o token e só então manda o navegador para /sua-conta/{service}. Enumerar
        // link nesse instante lê o menu da página de LOGIN, não o da área logada —
        // foi o que aconteceu na primeira medição, e é diferença que muda a
        // conclusão. Então: assentar a navegação, e só depois olhar o menu.
        JSONObject landing = new JSONObject();
        JSONArray destinations = new JSONArray();
        JSONArray routes = new JSONArray();
        // "Depois" só existe se houve medição depois. Repetir o "antes" aqui
        // pouparia um campo vazio e criaria um par before/after que parece
        // comparação e não é — o erro exato que este método existe para evitar.
        JSONObject after = new JSONObject();
        AgenciaWebLogin.BridgeState bridge = AgenciaWebLogin.BridgeState.NOT_TESTED;

        if (authenticated) {
            settle(deadline);
            landing = whereAmI();
            if (landing.optInt("chars", 0) == 0) {
                load(AgenciaWebLogin.ACCOUNT_URL, deadline);
                landing = whereAmI();
            }
            destinations = visibleDestinations();
            RodLog.step("ponte", "area autenticada em " + landing.optString("host", "")
                + landing.optString("path", "") + " caracteres=" + landing.optInt("chars", 0));
            record("area-autenticada", landing);
            // A enumeração inteira vai para o arquivo, e não só os links
            // seguidos: é ela que sustenta "o portal não oferece entrada
            // autenticada no host de faturas". Sem ela, a afirmação depende de
            // eu ter olhado, e ninguém pode conferir depois.
            record("destinos-visiveis", new JSONObject()
                .put("total", destinations.length()).put("lista", destinations));

            JSONArray links = officialServiceLinks();
            RodLog.step("ponte", "links oficiais de servico=" + links.length());
            for (int i = 0; i < links.length() && System.currentTimeMillis() < deadline; i++) {
                JSONObject landed = followOfficialLink(links.getJSONObject(i), deadline);
                // O que a página de destino pede é o veredito do link: se ela
                // devolve o formulário de login do ASPX, a sessão do go.* não
                // valeu ali — e isso é diferente de "o link não existe".
                JSONObject onLanding = probeStructure();
                landed.put("landing_login_form", onLanding.optBoolean("login_form", false));
                landed.put("landing_comboUC", onLanding.optBoolean("comboUC", false));
                landed.put("landing_chars", onLanding.optInt("chars", 0));
                landed.put("landing_notice", onLanding.optString("notice", ""));
                routes.put(landed);
                RodLog.step("ponte", "seguiu rotulo=" + landed.optString("word", "")
                    + " destino=" + landed.optString("host", "") + landed.optString("path", "")
                    + " pediu_login=" + landed.optBoolean("landing_login_form", false)
                    + " comboUC=" + landed.optBoolean("landing_comboUC", false)
                    + " caracteres=" + landed.optInt("landing_chars", 0));
                // Uma linha por rota, com rótulo, destino e o que o destino
                // pediu — juntos. Separar isso em linhas diferentes obrigaria
                // quem audita a correlacionar por horário, e horário é
                // exatamente o que se perde quando o buffer rotaciona.
                record("rota", new JSONObject()
                    .put("rotulo", landed.optString("word", ""))
                    .put("destino", landed.optString("host", "") + landed.optString("path", ""))
                    .put("pediu_login", landed.optBoolean("landing_login_form", false))
                    .put("comboUC", landed.optBoolean("landing_comboUC", false))
                    .put("caracteres", landed.optInt("landing_chars", 0))
                    .put("aviso", landed.optString("landing_notice", ""))
                    .put("origem", landed.optString("from_host", "")
                        + landed.optString("from_path", "")));
                after = probeBillArea(deadline);
                bridge = classifyBillArea(true, after);
                RodLog.step("ponte", "depois de " + landed.optString("word", "")
                    + ": " + describeBillArea(after) + " ponte=" + bridge);
                record("depois/segunda-via", new JSONObject(after.toString())
                    .put("depois_de", landed.optString("word", ""))
                    .put("ponte", bridge.name()));
                if (bridge == AgenciaWebLogin.BridgeState.OPEN) break;
            }
            if (routes.length() == 0) {
                after = probeBillArea(deadline);
                bridge = classifyBillArea(true, after);
                RodLog.step("ponte", "nenhum link oficial de servico: " + describeBillArea(after)
                    + " ponte=" + bridge);
                record("depois/segunda-via", new JSONObject(after.toString())
                    .put("depois_de", "nenhum link oficial").put("ponte", bridge.name()));
            }

            // O controle positivo repetido DEPOIS do login. É o par que responde
            // a pergunta da rodada: se a sessão do go.* valesse no host de
            // faturas, o formulário de login desta página teria sumido. Se ele
            // continua lá, a resposta é da concessionária e não do experimento.
            JSONObject afterHost = probeBillHostLogin(deadline);
            RodLog.step("ponte", "depois, controle no host de faturas: "
                + describeBillArea(afterHost));
            record("depois/logingo", afterHost);
            record("controle", new JSONObject()
                .put("host_de_faturas_respondeu", !afterHost.optBoolean("erro_de_rede", false)
                    && afterHost.optInt("chars", 0) > 0)
                .put("rotas_do_mesmo_host_diferem",
                    afterHost.optInt("chars", 0) != after.optInt("chars", 0)
                        || afterHost.optBoolean("login_form", false)
                            != after.optBoolean("login_form", false))
                .put("login_ainda_exigido_apos_sessao_go",
                    afterHost.optBoolean("login_form", false)));
        }

        record("veredito", new JSONObject()
            .put("ponte", bridge.name()).put("login_go", outcome.name()));
        if (evidence != null)
            RodLog.step("prova", "trilha gravada em " + evidence.getAbsolutePath());

        return new JSONObject()
            .put("evidence_file", evidence == null ? "" : evidence.getAbsolutePath())
            .put("go_outcome", outcome.name())
            .put("go_state", state.name())
            .put("go_session_was_already_open", already)
            .put("landing", landing)
            .put("destinations", destinations)
            .put("cookies_goias", cookiePresence(BILL_URL))
            .put("cookies_go", cookiePresence(AgenciaWebLogin.LOGIN_URL))
            .put("before", before)
            .put("after", after)
            .put("bridge", bridge.name())
            .put("routes", routes);
    }

    /**
     * Olha o {@code SegundaVia.aspx} e devolve só marcadores estruturais.
     *
     * Nem texto de página, nem {@code location.search}: o caminho é público, mas
     * o query string é justamente onde um portal põe identificador de sessão. O
     * que sai daqui é booleano, host e caminho — nada que sirva para entrar em
     * conta nenhuma.
     */
    private JSONObject probeBillArea(long deadline) throws Exception {
        load(BILL_URL, deadline);
        return probeStructure();
    }

    /**
     * Marcadores da página que já está carregada, sem navegar.
     *
     * Separado do carregamento porque a mesma leitura serve para dois lugares: a
     * área de faturas e a página em que um link oficial ATERRISSOU. Sem isso, a
     * medição saberia que o link levou ao host das faturas mas não saberia se a
     * página de lá pediu credencial de novo — que é exatamente a pergunta.
     */
    private JSONObject probeStructure() throws Exception {
        String script =
            "(function(){"
            + "function q(id){return !!document.getElementById(id);}"
            + "var t=document.body?document.body.innerText:'';"
            + "return JSON.stringify({"
            + "login_form:!!(document.getElementById('WEBDOOR_headercorporativogo_txtUC')"
            + "&&document.getElementById('WEBDOOR_headercorporativogo_txtDocumento')),"
            + "comboUC:q('CONTENT_comboBoxUC'),tipoEmissao:q('CONTENT_cbTipoEmissao'),"
            + "motivo:q('CONTENT_cbMotivo'),emitir:q('CONTENT_btEnviar'),"
            + "chars:t.trim().length,host:location.hostname,path:location.pathname,"
            + "text:t.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase()});"
            + "})()";
        JSONObject area = new JSONObject(evalJson(script));
        // O texto entra, vira palavra do vocabulário e SAI do objeto. Nada de
        // conteúdo de página sobrevive à fronteira deste método.
        String notice = AgenciaWebLogin.noticeWord(area.optString("text", ""));
        area.remove("text");
        // Erro de rede no documento principal e resposta curta do servidor são
        // coisas diferentes, e sem esta marca as duas viram "a pagina veio
        // vazia" — que foi exatamente a leitura que confundiu a rodada passada.
        return area.put("notice", notice).put("erro_de_rede", failed.get());
    }

    /** Onde a navegação está agora: host, caminho e tamanho. Nunca query string. */
    private JSONObject whereAmI() throws Exception {
        return new JSONObject(evalJson(
            "(function(){var t=document.body?document.body.innerText:'';"
            + "return JSON.stringify({host:location.hostname,path:location.pathname,"
            + "chars:t.trim().length});})()"));
    }

    /**
     * Há cookie para este host, e quantos — nada além da contagem.
     *
     * É a pergunta da rodada em forma estrutural: se o login do {@code go.*}
     * criasse sessão para o {@code goias.*}, ela chegaria como cookie de domínio
     * compartilhado, e a contagem mudaria. O VALOR nunca sai daqui: cookie de
     * sessão é credencial portadora, e imprimi-lo seria entregar a conta do
     * proprietário a qualquer coisa que leia o log.
     */
    private JSONObject cookiePresence(final String url) throws Exception {
        final ArrayBlockingQueue<String> answer = new ArrayBlockingQueue<>(1);
        main.post(new Runnable() {
            @Override public void run() {
                String raw = CookieManager.getInstance().getCookie(url);
                answer.offer(raw == null ? "" : raw);
            }
        });
        String raw = answer.poll(EVAL_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS);
        if (raw == null) raw = "";
        int count = 0;
        boolean aspSession = false;
        for (String part : raw.split(";")) {
            String name = part.trim();
            int eq = name.indexOf('=');
            if (eq > 0) name = name.substring(0, eq);
            if (name.isEmpty()) continue;
            count++;
            String folded = name.toLowerCase();
            if (folded.contains("asp.net_sessionid") || folded.contains("aspxauth")) {
                aspSession = true;
            }
        }
        return new JSONObject().put("count", count).put("asp_session", aspSession);
    }

    private static String describeBillArea(JSONObject area) {
        return "login_form=" + area.optBoolean("login_form", false)
            + " comboUC=" + area.optBoolean("comboUC", false)
            + " tipoEmissao=" + area.optBoolean("tipoEmissao", false)
            + " motivo=" + area.optBoolean("motivo", false)
            + " emitir=" + area.optBoolean("emitir", false)
            + " caracteres=" + area.optInt("chars", 0)
            + " destino=" + area.optString("host", "") + area.optString("path", "")
            + " aviso=" + area.optString("notice", "");
    }

    private static AgenciaWebLogin.BridgeState classifyBillArea(boolean authenticated,
                                                                JSONObject area) {
        return AgenciaWebLogin.bridge(authenticated,
            area.optBoolean("login_form", false), area.optBoolean("comboUC", false),
            area.optBoolean("tipoEmissao", false), area.optBoolean("motivo", false),
            area.optBoolean("emitir", false), area.optInt("chars", 0) > 0);
    }

    /**
     * Os links que um usuário veria e clicaria, e nada além deles.
     *
     * Filtra por VISIBILIDADE ({@code offsetParent} e retângulo) porque o portal
     * carrega menu duplicado para telas pequenas e âncora escondida de rodapé;
     * clicar no que ninguém vê não é navegação de usuário. Prioriza quem já
     * aponta para o host das faturas — se a ponte existir declarada, é o caminho
     * mais curto — e depois aceita link do próprio {@code go.*} cujo rótulo casa
     * o vocabulário de serviço.
     *
     * O rótulo NÃO sai daqui. O que sai é a palavra genérica do vocabulário que
     * ele casou, porque em área autenticada o menu costuma trazer o nome do
     * titular dentro do próprio texto do link.
     */
    private JSONArray officialServiceLinks() throws Exception {
        StringBuilder words = new StringBuilder();
        for (String word : AgenciaWebLogin.SERVICE_WORDS) {
            if (words.length() > 0) words.append(',');
            words.append(JSONObject.quote(word));
        }
        String script =
            "(function(){"
            + "var words=[" + words + "];"
            + "function fold(v){return (v||'').normalize('NFD')"
            + ".replace(/[\\u0300-\\u036f]/g,'').toLowerCase();}"
            + "function word(v){for(var i=0;i<words.length;i++)"
            + "if(v.indexOf(words[i])>=0) return words[i];return '';}"
            + "var out=[],seen={};"
            + "var all=document.querySelectorAll('a[href]');"
            + "for(var i=0;i<all.length;i++){var a=all[i];"
            + "if(!a.offsetParent) continue;"
            + "var r=a.getBoundingClientRect(); if(r.width===0||r.height===0) continue;"
            + "if(!/^https?:$/.test(a.protocol||'')) continue;"
            + "var host=a.hostname||'',path=a.pathname||'';"
            + "var cross=(host.indexOf(" + JSONObject.quote(AgenciaWebLogin.BILL_HOST) + ")>=0);"
            + "var w=word(fold(a.textContent))||word(fold(path));"
            + "if(!cross&&!w) continue;"
            + "var key=host+path; if(seen[key]) continue; seen[key]=1;"
            + "out.push({host:host,path:path,word:(w||'host das faturas'),"
            + "cross:cross,href:a.href});"
            + "}"
            + "out.sort(function(x,y){return (y.cross?1:0)-(x.cross?1:0);});"
            + "return JSON.stringify(out.slice(0,6));"
            + "})()";
        return new JSONArray(evalJson(script));
    }

    /**
     * Todos os destinos visíveis da página, por host e caminho, sem rótulo nenhum.
     *
     * O filtro por vocabulário existe para ESCOLHER onde clicar, e por isso ele
     * pode esconder um link cujo rótulo ninguém previu — e concluir "o portal não
     * oferece" a partir de uma busca por palavra seria concluir sobre a minha
     * lista, não sobre o portal. Esta enumeração é a prova negativa: ela mostra
     * tudo que a área autenticada oferece, e deixa o leitor conferir que nenhum
     * dos destinos é uma entrada autenticada no host das faturas.
     *
     * Sai host e caminho, jamais rótulo e jamais query string: o rótulo do menu
     * autenticado carrega o nome do titular, e o query string é onde vive token.
     */
    private JSONArray visibleDestinations() throws Exception {
        String script =
            "(function(){var out=[],seen={};"
            + "var all=document.querySelectorAll('a[href]');"
            + "for(var i=0;i<all.length;i++){var a=all[i];"
            + "if(!a.offsetParent) continue;"
            + "var r=a.getBoundingClientRect(); if(r.width===0||r.height===0) continue;"
            + "if(!/^https?:$/.test(a.protocol||'')) continue;"
            + "var key=(a.hostname||'')+(a.pathname||'');"
            + "if(seen[key]) continue; seen[key]=1;"
            + "out.push({host:a.hostname||'',path:a.pathname||''});}"
            + "return JSON.stringify(out.slice(0,60));})()";
        return new JSONArray(evalJson(script));
    }

    /**
     * Segue um link como o navegador seguiria: o {@code href} que a página já tem.
     *
     * Nada é montado. O endereço vem da âncora visível, e redirect do servidor é
     * bem-vindo — é justamente o que a medição quer observar. Devolve onde a
     * navegação parou: host e caminho, jamais o query string.
     */
    private JSONObject followOfficialLink(JSONObject link, long deadline) throws Exception {
        String href = link.optString("href", "");
        if (href.isEmpty())
            return new JSONObject().put("word", link.optString("word", ""))
                .put("host", "").put("path", "");
        load(href, Math.min(deadline, System.currentTimeMillis() + LOAD_TIMEOUT_MILLIS));
        JSONObject where = new JSONObject(evalJson(
            "JSON.stringify({host:location.hostname,path:location.pathname})"));
        return new JSONObject()
            .put("word", link.optString("word", ""))
            .put("from_host", link.optString("host", ""))
            .put("from_path", link.optString("path", ""))
            .put("host", where.optString("host", ""))
            .put("path", where.optString("path", ""));
    }

    /** Escolhe a unidade no combo da segunda via, casando dígitos como o outro motor. */
    private void selectUnit(String unit, long deadline) throws Exception {
        String script =
            "(function(){"
            + "var s=document.getElementById('CONTENT_comboBoxUC');"
            + "if(!s) return JSON.stringify({found:false,options:0});"
            + "function norm(v){v=(v||'').replace(/\\D/g,'');return v.replace(/^0+(?!$)/,'');}"
            + "var want=norm(" + JSONObject.quote(unit) + ");"
            + "for(var i=0;i<s.options.length;i++){"
            + "if(norm(s.options[i].text)===want||norm(s.options[i].value)===want){"
            + "s.selectedIndex=i;s.dispatchEvent(new Event('change',{bubbles:true}));"
            + "return JSON.stringify({found:true,options:s.options.length});}}"
            + "return JSON.stringify({found:false,options:s.options.length});"
            + "})()";
        JSONObject picked = new JSONObject(evalJson(script));
        RodLog.step("webview", "unidades no combo=" + picked.optInt("options", -1)
            + " imovel escolhido=" + picked.optBoolean("found", false));
        if (!picked.optBoolean("found", false))
            throw new IllegalStateException(picked.optInt("options", 0) > 0
                ? "EQUATORIAL_PROPERTY_NOT_MAPPED: a unidade configurada nao esta entre as "
                    + picked.optInt("options", 0) + " deste login"
                : "EQUATORIAL_CONTRACT_NOT_FOUND: a lista de unidades veio vazia");
        settle(deadline);
    }

    /**
     * Preenche tipo e motivo e emite.
     *
     * "Apenas código de barras" renderiza o dado na própria página; "fatura
     * completa" baixa um PDF, que este motor não sabe ler. O motivo é "Outros",
     * pelo mesmo critério do outro motor: é a única opção que não afirma algo
     * falso em nome do proprietário.
     */
    private void emit(long deadline) throws Exception {
        emit(deadline, new String[]{"codigo de barras", "código de barras"});
    }

    /**
     * "Fatura completa" leva a SegundaViaDownload.aspx, que e onde vive o QR do PIX.
     *
     * A leitura normal nao usa esta rota porque ela produz PDF, e PDF nao se le
     * pela arvore. Para o PIX ela e obrigatoria: o base64 do QR esta no onclick do
     * controle da linha, no DOM daquela pagina.
     */
    private void emitFullInvoice(long deadline) throws Exception {
        emit(deadline, new String[]{"fatura completa", "completa"});
    }

    /** Emite o tipo pedido; as palavras viajam para o JS como literais JSON. */
    private void emit(long deadline, String[] typeWords) throws Exception {
        StringBuilder words = new StringBuilder();
        for (String word : typeWords) {
            if (words.length() > 0) words.append(',');
            words.append(JSONObject.quote(word.toLowerCase()));
        }
        String script =
            "(function(){"
            + "function pick(id,words){var s=document.getElementById(id);"
            + "if(!s) return false;"
            + "for(var i=0;i<s.options.length;i++){"
            + "var t=(s.options[i].text||'').toLowerCase();"
            + "for(var w=0;w<words.length;w++) if(t.indexOf(words[w])>=0){"
            + "s.selectedIndex=i;s.dispatchEvent(new Event('change',{bubbles:true}));return true;}}"
            + "return false;}"
            + "var tipo=pick('CONTENT_cbTipoEmissao',[" + words + "]);"
            + "var motivo=pick('CONTENT_cbMotivo',['outros']);"
            + "var b=document.getElementById('CONTENT_btEnviar');"
            + "if(b) b.click();"
            + "return JSON.stringify({tipo:tipo,motivo:motivo,enviado:!!b});"
            + "})()";
        JSONObject form = new JSONObject(evalJson(script));
        RodLog.step("webview", "tipo=" + form.optBoolean("tipo", false)
            + " motivo=" + form.optBoolean("motivo", false)
            + " emitir=" + form.optBoolean("enviado", false));
        if (!form.optBoolean("enviado", false))
            throw new IllegalStateException(
                "EQUATORIAL_BILL_NOT_FOUND: botao Emitir ausente no motor WebView");
        settle(deadline);
    }

    /** Lê a fatura do DOM e delega a extração ao parser, que é o mesmo dos dois motores. */
    private JSONObject readBill(String expectedUnit) throws Exception {
        String text = evalJson("document.body?document.body.innerText:''");
        EquatorialTextParser.Page page = EquatorialTextParser.parse(text);
        if (page.state == EquatorialTextParser.State.AUTH_REQUIRED)
            throw new IllegalStateException(
                EquatorialSession.errorFor(EquatorialSession.Decision.FAIL_EXHAUSTED));
        if (page.state == EquatorialTextParser.State.HUMAN_CHECK)
            throw new IllegalStateException(
                "EQUATORIAL_HUMAN_CHECK: a Equatorial pediu verificacao humana no motor WebView");
        if (page.state == EquatorialTextParser.State.NO_BILL)
            throw new IllegalStateException(
                "EQUATORIAL_BILL_NOT_FOUND: nenhuma fatura visivel no motor WebView");

        String shown = page.get("uc").replaceAll("\\D", "");
        String expected = expectedUnit == null ? "" : expectedUnit.replaceAll("\\D", "");
        if (!shown.isEmpty() && !expected.isEmpty()
            && !EquatorialSession.sameUnit(shown, expected)) {
            RodLog.fail("webview", "a pagina mostra outro imovel");
            throw new IllegalStateException(
                "EQUATORIAL_CONTRACT_NOT_FOUND: a tela mostra outra unidade consumidora");
        }
        RodLog.found("webview", "valor", !page.get("amount").isEmpty());
        RodLog.found("webview", "vencimento", !page.get("due_date").isEmpty());
        RodLog.found("webview", "referencia", !page.get("reference").isEmpty());
        RodLog.found("webview", "codigo de barras", !page.get("barcode").isEmpty());
        RodLog.found("webview", "pix", !page.get("pix").isEmpty());
        return new JSONObject()
            .put("source", "equatorial_rod_webview")
            .put("amount", page.get("amount"))
            .put("due_date", page.get("due_date"))
            .put("reference", page.get("reference"))
            .put("barcode", page.get("barcode"))
            .put("pix", page.get("pix"));
    }

    // --------------------------------------------------------------- observação

    private static final class Observation {
        final EquatorialSession.State state;
        final boolean billSelector;
        Observation(EquatorialSession.State state, boolean billSelector) {
            this.state = state;
            this.billSelector = billSelector;
        }
    }

    /**
     * Uma olhada no DOM traduzida no vocabulário da máquina de sessão.
     *
     * A presença dos elementos é estrutural — getElementById, não texto — porque
     * o rodapé do portal fala de login em toda página, inclusive nas autenticadas.
     */
    private Observation observe(boolean afterSubmit) throws Exception {
        String script =
            "(function(){"
            + "var bill=!!document.getElementById('CONTENT_comboBoxUC');"
            + "var login=!!(document.getElementById('WEBDOOR_headercorporativogo_txtUC')"
            + "&&document.getElementById('WEBDOOR_headercorporativogo_txtDocumento'));"
            + "var t=document.body?document.body.innerText:'';"
            + "return JSON.stringify({bill:bill,login:login,text:t});"
            + "})()";
        JSONObject dom;
        try {
            dom = new JSONObject(evalJson(script));
        } catch (Exception error) {
            return new Observation(EquatorialSession.State.BROWSER_STALE, false);
        }
        boolean bill = dom.optBoolean("bill", false);
        boolean login = dom.optBoolean("login", false);
        String text = dom.optString("text", "");
        boolean responding = !failed.get() && (bill || login || !text.trim().isEmpty());
        EquatorialSession.State state =
            EquatorialSession.classify(text, responding, bill, login, afterSubmit);
        return new Observation(state, bill);
    }

    // ------------------------------------------------------------------ WebView

    private void create(final Context context) throws Exception {
        final ArrayBlockingQueue<Object> ready = new ArrayBlockingQueue<>(1);
        main.post(new Runnable() {
            @Override public void run() {
                try {
                    CookieManager.getInstance().setAcceptCookie(true);
                    WebView view = new WebView(context);
                    WebSettings settings = view.getSettings();
                    settings.setJavaScriptEnabled(true);
                    settings.setDomStorageEnabled(true);
                    settings.setDatabaseEnabled(true);
                    settings.setLoadsImagesAutomatically(false);
                    settings.setBlockNetworkImage(true);
                    view.setWebViewClient(new WebViewClient() {
                        @Override public void onPageFinished(WebView source, String url) {
                            loads.incrementAndGet();
                        }
                        @Override public void onReceivedError(WebView source, WebResourceRequest request,
                                                             WebResourceError error) {
                            // Só o documento principal importa; recurso secundário
                            // falhando não invalida a página.
                            if (request != null && request.isForMainFrame()) {
                                failed.set(true);
                                RodLog.fail("webview", "erro de rede no documento principal");
                            }
                        }
                    });
                    // Sem janela, nada é medido: sem medir, o layout não existe e
                    // scripts que dependem de dimensão não rodam.
                    view.measure(
                        View.MeasureSpec.makeMeasureSpec(VIEWPORT_WIDTH, View.MeasureSpec.EXACTLY),
                        View.MeasureSpec.makeMeasureSpec(VIEWPORT_HEIGHT, View.MeasureSpec.EXACTLY));
                    view.layout(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT);
                    webView = view;
                    ready.offer(Boolean.TRUE);
                } catch (Throwable error) {
                    ready.offer(error);
                }
            }
        });
        Object outcome = ready.poll(EVAL_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS);
        if (outcome instanceof Throwable)
            throw new IllegalStateException("EQUATORIAL_WEBVIEW_UNAVAILABLE: "
                + ((Throwable) outcome).getClass().getSimpleName());
        if (outcome == null)
            throw new IllegalStateException("EQUATORIAL_WEBVIEW_UNAVAILABLE: motor nao iniciou");
    }

    private void destroy() {
        main.post(new Runnable() {
            @Override public void run() {
                if (webView != null) {
                    webView.stopLoading();
                    webView.destroy();
                    webView = null;
                }
            }
        });
    }

    private void dropOwnSessionInline() {
        main.post(new Runnable() {
            @Override public void run() {
                CookieManager cookies = CookieManager.getInstance();
                cookies.removeSessionCookies(null);
                cookies.flush();
                if (webView != null) webView.clearCache(true);
            }
        });
    }

    /**
     * Navega e espera a PAGINA NOVA, nao qualquer pagina completa.
     *
     * Esperar apenas por readyState "complete" devolvia na hora: o documento
     * anterior ja estava completo antes de a navegacao nova nem comecar, e a
     * observacao seguinte classificava a pagina errada. Contar os fins de
     * carregamento resolve, porque um deles so acontece depois da troca.
     */
    private void load(final String url, long deadline) throws Exception {
        failed.set(false);
        int before = loads.get();
        main.post(new Runnable() {
            @Override public void run() { if (webView != null) webView.loadUrl(url); }
        });
        settle(deadline, before);
    }

    /** Espera a página assentar. Estado observável, nunca tempo fixo. */
    private void settle(long deadline) throws Exception {
        settle(deadline, loads.get() - 1);
    }

    private void settle(long deadline, int loadsBefore) throws Exception {
        long limit = Math.min(deadline, System.currentTimeMillis() + LOAD_TIMEOUT_MILLIS);
        while (System.currentTimeMillis() < limit) {
            Thread.sleep(POLL_MILLIS);
            if (failed.get()) return;
            if (loads.get() <= loadsBefore) continue;
            String state;
            try {
                state = evalJson("document.readyState");
            } catch (Exception ignored) {
                continue;
            }
            if ("complete".equals(state)) return;
        }
    }

    /**
     * Avalia JS e devolve a string que o script serializou.
     *
     * evaluateJavascript entrega JSON, então uma string vem entre aspas e com
     * escapes. Desembrulhar aqui evita que cada chamador repita esse cuidado — e
     * um deles esquecia, o que fazia o parser receber a página com \n literais.
     */
    private String evalJson(final String script) throws Exception {
        final ArrayBlockingQueue<String> answer = new ArrayBlockingQueue<>(1);
        main.post(new Runnable() {
            @Override public void run() {
                if (webView == null) { answer.offer("null"); return; }
                webView.evaluateJavascript(script, value -> answer.offer(value == null ? "null" : value));
            }
        });
        String raw = answer.poll(EVAL_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS);
        if (raw == null)
            throw new IllegalStateException("EQUATORIAL_PORTAL_TIMEOUT: motor WebView nao respondeu");
        return unwrap(raw);
    }

    /** Desembrulha o valor JSON devolvido por evaluateJavascript. */
    static String unwrap(String raw) throws Exception {
        if (raw == null || raw.equals("null")) return "";
        return new JSONObject("{\"v\":" + raw + "}").optString("v", "");
    }
}
