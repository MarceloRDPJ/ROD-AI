package br.com.jarviscerrado.poco;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.PowerManager;
import java.util.UUID;
import org.json.JSONObject;

/**
 * Leitura da fatura da Equatorial, com sessão mantida pelo próprio ROD.
 *
 * A versão anterior dependia de a pessoa entrar no portal à mão de tempos em
 * tempos: quando a sessão caía, a consulta devolvia EQUATORIAL_AUTH_REQUIRED e
 * parava. Isso transformava uma consulta automática em tarefa humana com hora
 * marcada, e à noite simplesmente não havia resposta.
 *
 * Agora expirar é um estado do caminho, não o fim dele. Quem decide o que fazer
 * com cada estado é {@link EquatorialSession}, que existe separada e testável
 * justamente porque as duas regras que ela guarda são de segurança e não podem
 * ficar diluídas no meio do fluxo: no máximo duas tentativas de login por job, e
 * credencial recusada encerra na hora.
 *
 * Há dois motores. O principal usa o Chrome do aparelho por acessibilidade, onde
 * limpar qualquer coisa está proibido — os cookies ali são de todos os sites do
 * proprietário. O alternativo é o {@link EquatorialWebEngine}, WebView do próprio
 * app, onde limpar cookie e cache é a recuperação correta porque não há mais nada
 * dentro dele.
 *
 * Nenhum passo espera por tempo fixo: cada um aguarda um estado observável com
 * prazo próprio, porque tempo fixo ora desperdiça segundos ora corta o
 * carregamento no meio.
 */
final class EquatorialReader {
    /** Cada passo já espera por estado internamente; esta é a rede de segurança. */
    static final long CALL_TIMEOUT_MILLIS = 60_000L;
    /** abrir, fechar aviso, selecionar imóvel, ler: cada um pode gastar o timeout inteiro. */
    static final int FLOW_STEPS = 4;
    /** Margem para o broadcast, o despertar da tela e a montagem da resposta. */
    private static final long BUDGET_MARGIN_MILLIS = 30_000L;
    /**
     * Cobre o pior caso do fluxo inteiro; a tela precisa ficar acesa até o fim.
     *
     * Eram 180 s fixos para quatro chamadas de 60 s: no pior caso o wake lock caía
     * antes do último passo, e a leitura falhava por tela apagada e não por portal.
     * O valor agora é derivado, para não voltar a divergir do fluxo.
     */
    static final long SCREEN_BUDGET_MILLIS =
        screenBudget(CALL_TIMEOUT_MILLIS, FLOW_STEPS, BUDGET_MARGIN_MILLIS);

    /**
     * Prazo do job inteiro, do lado do Android.
     *
     * O Pi desiste em 240 s. Terminar aqui um pouco antes é o que permite devolver
     * um erro tipado — que o proprietário entende — em vez de o job simplesmente
     * expirar sem explicação.
     */
    static final long JOB_BUDGET_MILLIS = 200_000L;
    /** Abaixo disso não vale começar o motor alternativo: ele não terminaria. */
    static final long WEBVIEW_MIN_MILLIS = 60_000L;
    /** Voltas na máquina de estados. Cada volta consome tentativa ou termina. */
    static final int MAX_ROUNDS = 10;

    static long screenBudget(long callTimeout, int steps, long margin) {
        return callTimeout * steps + margin;
    }

    private EquatorialReader() { }

    static JSONObject read(Context context, String property) throws Exception {
        BillingConfig config = BillingConfig.load(context);
        String normalized = normalizeProperty(property);
        String unit = digits(config.value(normalized + "_energy"));
        // O documento do titular volta ao fluxo porque o login automático voltou.
        // Ele sai do cofre e vai direto ao campo do portal: não é registrado, não é
        // devolvido no payload e não aparece na trilha.
        String document = digits(config.value("equatorial_cpf"));
        if (unit.isEmpty())
            throw new IllegalStateException("Unidade consumidora da Equatorial nao configurada para " + normalized);

        EquatorialSession session = new EquatorialSession(!unit.isEmpty() && !document.isEmpty());
        long deadline = System.currentTimeMillis() + JOB_BUDGET_MILLIS;

        PowerManager.WakeLock wake = context.getSystemService(PowerManager.class).newWakeLock(
            PowerManager.SCREEN_BRIGHT_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP, "rod:equatorial-read");
        wake.acquire(SCREEN_BUDGET_MILLIS);
        try {
            JSONObject result = drive(context, session, unit, document, deadline);
            result.put("property", normalized).put("read_only", true);
            return result;
        } finally {
            if (wake.isHeld()) wake.release();
        }
    }

    /**
     * Conduz a consulta pelos estados da sessão até um resultado ou um erro tipado.
     *
     * O laço tem teto e cada desvio consome uma tentativa registrada na sessão, e
     * não uma contagem local: é isso que garante que "tentar de novo" nunca vire
     * insistência contra a conta do proprietário.
     */
    private static JSONObject drive(Context context, EquatorialSession session,
                                    String unit, String document, long deadline) throws Exception {
        call(context, "open_equatorial", null, null);
        call(context, "dismiss_equatorial", null, null);

        JSONObject observation = null;
        for (int round = 0; round < MAX_ROUNDS; round++) {
            if (System.currentTimeMillis() > deadline)
                throw new IllegalStateException(
                    "EQUATORIAL_PORTAL_TIMEOUT: o job esgotou o tempo antes de concluir a leitura");
            if (observation == null) observation = call(context, "session_equatorial", null, null);
            EquatorialSession.State state = stateOf(observation);
            boolean selector = observation.optBoolean("selector", false);
            observation = null;

            EquatorialSession.Decision decision = session.observe(state);
            RodLog.step("sessao", "estado=" + state + " decisao=" + decision
                + " seletor=" + selector + " logins=" + session.loginAttempts());

            switch (decision) {
                case PROCEED:
                    if (!selector) {
                        // Autenticado, porém fora da segunda via: entrar no portal
                        // desemboca na home da área logada, não na rota da fatura.
                        RodLog.step("sessao", "dentro do portal, voltando para a segunda via");
                        observation = callMode(context, "recover_equatorial", "reload");
                        break;
                    }
                    try {
                        return readWithChrome(context, unit);
                    } catch (IllegalStateException error) {
                        if (!expired(error.getMessage())) throw error;
                        // A sessão caiu no meio do caminho: o portal devolveu a tela
                        // de login depois de já estar dentro. É estado, não falha.
                        RodLog.step("sessao", "sessao caiu durante a leitura");
                        observation = expiredObservation();
                    }
                    break;
                case LOGIN:
                    // Duas tentativas, duas ESCRITAS do mesmo numero — nunca outra
                    // credencial. O portal nao diz nada quando recusa (conferido:
                    // uma tentativa com unidade inexistente devolve exatamente a
                    // mesma tela em branco), entao nao ha como saber por qual das
                    // duas formas ele espera a unidade.
                    observation = call(context, "login_equatorial",
                        session.loginAttempts() >= EquatorialSession.MAX_LOGIN_ATTEMPTS
                            ? portalUnit(unit) : unit,
                        document);
                    break;
                case RELOAD_ROUTE:
                    observation = callMode(context, "recover_equatorial", "reload");
                    break;
                case REOPEN_TAB:
                    observation = callMode(context, "recover_equatorial", "reopen");
                    break;
                case FALLBACK_WEBVIEW:
                    session.markWebViewTried();
                    if (System.currentTimeMillis() + WEBVIEW_MIN_MILLIS > deadline)
                        throw new IllegalStateException(
                            EquatorialSession.errorFor(EquatorialSession.Decision.FAIL_EXHAUSTED));
                    RodLog.step("sessao", "trocando para o motor WebView do ROD");
                    return EquatorialWebEngine.read(context, unit, document, deadline);
                default:
                    throw new IllegalStateException(EquatorialSession.errorFor(decision));
            }
        }
        throw new IllegalStateException(
            EquatorialSession.errorFor(EquatorialSession.Decision.FAIL_EXHAUSTED));
    }

    /** Selecionar o imóvel, emitir e ler, pela sessão do Chrome. */
    private static JSONObject readWithChrome(Context context, String unit) throws Exception {
        call(context, "select_equatorial", unit, null);
        call(context, "emit_equatorial", null, null);
        return call(context, "read_equatorial", unit, null);
    }

    /**
     * A falha diz que o portal voltou a pedir autenticação?
     *
     * O passo de seleção e o de leitura descobrem a sessão caída antes de qualquer
     * probe, porque é neles que a página é lida por inteiro. Reconhecer o código
     * aqui é o que fecha o ciclo: em vez de propagar o erro ao Pi, a máquina de
     * estados reautentica e repete.
     */
    static boolean expired(String message) {
        return message != null && message.contains("EQUATORIAL_AUTH_REQUIRED");
    }

    /** A leitura descobriu sessão caída; volta ao laço como observação, não como erro. */
    private static JSONObject expiredObservation() throws Exception {
        return new JSONObject()
            .put("state", EquatorialSession.State.SESSION_EXPIRED.name())
            .put("selector", false);
    }

    /** Estado devolvido por um passo de sessão; ilegível conta como navegador ruim. */
    static EquatorialSession.State stateOf(JSONObject payload) {
        String name = payload == null ? "" : payload.optString("state", "");
        try {
            return EquatorialSession.State.valueOf(name);
        } catch (Exception ignored) {
            return EquatorialSession.State.BROWSER_STALE;
        }
    }

    private static JSONObject callMode(Context context, String operation, String mode) throws Exception {
        return dispatch(context, operation, null, null, mode);
    }

    private static JSONObject call(Context context, String operation, String unit, String document)
            throws Exception {
        return dispatch(context, operation, unit, document, null);
    }

    private static JSONObject dispatch(Context context, String operation, String unit,
                                       String document, String mode) throws Exception {
        RodLog.step("equatorial", "passo: " + operation);
        String request = UUID.randomUUID().toString();
        Intent intent = new Intent(JarvisAccessibilityService.ACTION_BRIDGE)
            .setPackage(context.getPackageName())
            .putExtra("request_id", request)
            .putExtra("operation", operation);
        if (unit != null) intent.putExtra("unit", unit);
        if (document != null) intent.putExtra("document", document);
        if (mode != null) intent.putExtra("mode", mode);
        context.sendBroadcast(intent);

        SharedPreferences prefs = context.getSharedPreferences(
            JarvisAccessibilityService.PREFS_BRIDGE, Context.MODE_PRIVATE);
        long deadline = System.currentTimeMillis() + CALL_TIMEOUT_MILLIS;
        while (System.currentTimeMillis() < deadline) {
            if (request.equals(prefs.getString("request_id", ""))) {
                if (!prefs.getBoolean("ok", false))
                    throw new IllegalStateException(prefs.getString("error", "Falha Equatorial"));
                return new JSONObject(prefs.getString("payload", "{}"));
            }
            Thread.sleep(100);
        }
        throw new IllegalStateException("Automacao Equatorial nao respondeu no passo " + operation);
    }

    private static String digits(String value) {
        return value == null ? "" : value.replaceAll("\\D", "");
    }

    /**
     * A mesma unidade consumidora escrita como o portal a escreve: quinze dígitos.
     *
     * O cofre guarda o número como o proprietário o conhece, com onze dígitos; a
     * tela do portal mostra quinze, completados com zeros à esquerda, e o campo de
     * acesso aceita quinze. Não é outro número — é o mesmo com outra grafia, e é
     * por isso que tentar a segunda grafia cabe dentro do teto de duas tentativas
     * sem virar adivinhação de credencial.
     */
    static String portalUnit(String unit) {
        String value = digits(unit);
        if (value.isEmpty() || value.length() >= PORTAL_UNIT_DIGITS) return value;
        StringBuilder padded = new StringBuilder();
        while (padded.length() < PORTAL_UNIT_DIGITS - value.length()) padded.append('0');
        return padded.append(value).toString();
    }

    /** Largura da unidade consumidora na Agência Virtual. */
    static final int PORTAL_UNIT_DIGITS = 15;

    static String normalizeProperty(String value) {
        String p = value == null ? "casa" : value.toLowerCase().replace(' ', '_');
        if (p.contains("kitnet") && (p.contains("1") || p.endsWith("01"))) return "kitnet_01";
        if (p.contains("kitnet") && (p.contains("2") || p.endsWith("02"))) return "kitnet_02";
        if (p.contains("sala")) return "sala_comercial";
        if (p.contains("restaurante")) return "restaurante";
        return "casa";
    }
}
