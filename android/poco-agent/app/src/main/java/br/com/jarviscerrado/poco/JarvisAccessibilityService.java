package br.com.jarviscerrado.poco;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.graphics.Bitmap;
import android.graphics.ColorSpace;
import android.graphics.Path;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Browser;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.HashMap;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;
import org.json.JSONObject;

public class JarvisAccessibilityService extends AccessibilityService {
    static final String ACTION_BRIDGE = "br.com.jarviscerrado.poco.ACCESSIBILITY_BRIDGE";
    static final String PREFS_BRIDGE = "accessibility_bridge";
    private static final String SANEAGO = "br.com.saneago";
    private static final String CHROME = "com.android.chrome";
    private static final String WHATSAPP = "com.whatsapp";

    /**
     * Aplicativo oficial da concessionária — o quinto canal da cadeia.
     *
     * Está aqui só para ser SONDADO: a pergunta é se ele guarda uma sessão viva,
     * porque sessão viva dispensa login e login é justamente o que os outros
     * canais tiveram recusado. A sondagem lê marcador estrutural e não toca em
     * nada — nenhum campo preenchido, nenhum botão acionado.
     */
    private static final String EQUATORIAL_APP = "com.equatorialenergia";
    /**
     * Segunda via na Agencia Virtual, alcancada direto pela sessao existente.
     *
     * A home institucional nao tem fatura nenhuma: so define contexto. Esta e a
     * pagina que realmente emite, e abri-la por Intent preserva os cookies, o que
     * elimina todos os cliques intermediarios.
     */
    /**
     * Rota exata da segunda via, capturada do location.href com a sessao viva.
     *
     * Uma tentativa anterior concluiu que este caminho nao era alcancavel por URL.
     * A conclusao estava errada: os testes rodaram com a sessao da Agencia Virtual
     * ja expirada, entao o que falhava era a sessao, nao a rota. Com sessao valida
     * o Intent abre a pagina autenticada direto, o que dispensa navegar por menus.
     *
     * Este host mantem sessao propria, separada do portal institucional: estar
     * logado la nao vale aqui.
     */
    private static final String SEGUNDA_VIA_URL =
        "https://goias.equatorialenergia.com.br/AgenciaGO/Servi%C3%A7os/aberto/SegundaVia.aspx";

    /** Prazo total para a pagina da Equatorial assentar num estado legivel. */
    private static final long PAGE_SETTLE_MILLIS = 25_000L;
    /** Intervalo entre releituras da arvore enquanto a pagina carrega. */
    private static final long PAGE_POLL_MILLIS = 600L;
    /** Prazo para a home autenticada renderizar o seletor de imovel. */
    private static final long CHOOSER_RENDER_MILLIS = 12_000L;
    /** Prazo para a lista nativa de contratos aparecer depois do toque. */
    /**
     * Prazo do login na Agência Web. Cabe dentro dos 240 s que o Pi espera, com
     * folga para o motor subir, o reCAPTCHA resolver a promessa e o portal
     * responder — e curto o bastante para sobrar tempo de reportar a recusa.
     */
    private static final long AGENCIAWEB_BUDGET_MILLIS = 120_000L;
    /**
     * Prazo do experimento da ponte: mais largo que o do login porque ele faz
     * quatro navegações — faturas antes, login, link oficial, faturas depois — e
     * cada uma delas tem prazo próprio de carregamento.
     */
    private static final long BRIDGE_BUDGET_MILLIS = 240_000L;

    private static final long DIALOG_OPEN_MILLIS = 5_000L;
    /** Prazo para a lista nativa sair da frente depois da escolha. */
    private static final long DIALOG_CLOSE_MILLIS = 6_000L;
    /** Intervalo entre releituras da arvore nas transicoes curtas. */
    private static final long UI_POLL_MILLIS = 400L;
    /** Toques no seletor de imovel antes de desistir. */
    private static final int CONTRACT_OPEN_ATTEMPTS = 3;
    /** BACKs no dialogo nativo antes de desistir de fecha-lo. */
    private static final int CONTRACT_CLOSE_ATTEMPTS = 3;
    /**
     * Ids do dialogo nativo com que o Chrome desenha um &lt;select&gt;.
     *
     * A lista de contratos do portal nao e conteudo web: e uma janela do sistema,
     * e enquanto ela esta na frente packageRoot(CHROME) devolve null.
     */
    private static final String[] DIALOG_VIEW_IDS = {
        "id/parentPanel", "id/customPanel", "id/select_dialog_listview", "id/text1"
    };

    private final BroadcastReceiver bridge = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String request = intent.getStringExtra("request_id");
            String operation = intent.getStringExtra("operation");
            if (request == null || operation == null) return;
            RodLog.step("ponte", "operacao=" + operation);
            try {
                if (operation.equals("open_saneago")) {
                    bringSaneagoToFront(request);
                } else if (operation.equals("read_saneago")) {
                    if (Build.VERSION.SDK_INT >= 31) performGlobalAction(GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE);
                    new Handler(Looper.getMainLooper()).postDelayed(() -> readSaneagoWithFallback(request), 1800);
                } else if (operation.equals("login_saneago")) {
                    loginSaneago(request, intent.getStringExtra("login"), intent.getStringExtra("password"), 0);
                } else if (operation.equals("select_saneago")) {
                    selectSaneago(request, intent.getStringExtra("account"), 0);
                } else if (operation.equals("open_equatorial")) {
                    openEquatorial(request, 0);
                } else if (operation.equals("dismiss_equatorial")) {
                    dismissEquatorialOverlay(request);
                } else if (operation.equals("session_equatorial")) {
                    probeEquatorialSession(request, false,
                        System.currentTimeMillis() + CHOOSER_RENDER_MILLIS);
                } else if (operation.equals("login_equatorial")) {
                    loginEquatorial(request, intent.getStringExtra("unit"),
                        intent.getStringExtra("document"), 0);
                } else if (operation.equals("agenciaweb_equatorial")) {
                    loginAgenciaWeb(request, intent.getStringExtra("property"));
                } else if (operation.equals("debts_equatorial")) {
                    readDebtsAgenciaWeb(request, intent.getStringExtra("property"));
                } else if (operation.equals("bridge_equatorial")) {
                    bridgeAgenciaWeb(request, intent.getStringExtra("property"));
                } else if (operation.equals("chrome_go_equatorial")) {
                    readDebtsChromeGo(request, intent.getStringExtra("mode"),
                        intent.getStringExtra("unit"));
                } else if (operation.equals("holder_units_equatorial")) {
                    discoverHolderUnits(request, intent.getStringExtra("unit"));
                } else if (operation.equals("probe_app_equatorial")) {
                    probeEquatorialApp(request);
                } else if (operation.equals("recover_equatorial")) {
                    recoverChrome(request, intent.getStringExtra("mode"), 0);
                } else if (operation.equals("select_equatorial")) {
                    selectEquatorialContract(request, intent.getStringExtra("unit"), 0);
                } else if (operation.equals("emit_equatorial")) {
                    emitEquatorial(request);
                } else if (operation.equals("read_equatorial")) {
                    readEquatorial(request, intent.getStringExtra("unit"));
                } else if (operation.equals("pix_equatorial")) {
                    PixBridge.pixPayload(JarvisAccessibilityService.this,
                        intent.getStringExtra("reference"), bridgeReply(request));
                } else if (operation.equals("boleto_equatorial")) {
                    BoletoBridge.invoiceIndex(JarvisAccessibilityService.this,
                        intent.getStringExtra("reference"), bridgeReply(request));
                } else if (operation.equals("install_whatsapp")) {
                    installWhatsAppFromPlayStore(request);
                } else if (operation.equals("setup_whatsapp_companion")) {
                    setupWhatsAppCompanion(request, 0, System.currentTimeMillis() + 90_000L);
                } else if (operation.equals("clara_equatorial")) {
                    startClaraConversation(request, intent.getStringExtra("unit"),
                        intent.getStringExtra("document"), intent.getStringExtra("birth"));
                } else reply(request, false, null, "Operacao nao permitida");
            } catch (Exception error) {
                reply(request, false, null, error.getClass().getSimpleName() + ": " + error.getMessage());
            }
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        IntentFilter filter = new IntentFilter(ACTION_BRIDGE);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(bridge, filter, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(bridge, filter);
    }
    /**
     * Segundo caminho de religamento do agente, independente do primeiro.
     *
     * O sistema rebinda este servico sozinho depois de `adb install -r` e depois
     * do boot: foi exatamente o que o `dumpsys activity services` mostrou quando
     * o no estava offline — a acessibilidade de pe, o AgentService ausente. O
     * rebind ja acontecia e nao era aproveitado.
     *
     * Vale como segundo caminho porque nao passa pelo mesmo porteiro. O autostart
     * da MIUI e um toggle por app no Security Center, nao concedivel por codigo,
     * e quando desligado descarta BOOT_COMPLETED — e em varias versoes
     * MY_PACKAGE_REPLACED junto — antes de chegar a um receiver de manifesto. A
     * permissao de acessibilidade foi concedida por outra porta e ja esta de pe.
     *
     * Cinto e suspensorio, nao substituicao: o BootReceiver continua sendo o
     * mecanismo declarado, e este aqui so pega carona num bind que o sistema faz
     * de qualquer jeito. Nao esta provado que componente ligado pelo sistema
     * ganhe isencao de inicio de servico em primeiro plano; se nao ganhar,
     * AgentService.start devolve false e escreve o motivo na trilha, em vez de
     * derrubar o servico de acessibilidade junto.
     */
    @Override protected void onServiceConnected() {
        super.onServiceConnected();
        RodLog.step("autostart", "causa=acessibilidade servico_iniciado=" + AgentService.start(this));
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { }
    @Override public void onDestroy() { unregisterReceiver(bridge); super.onDestroy(); }

    /**
     * Leva o WhatsApp oficial ate o QR de aparelho adicional, sem numero/SIM.
     *
     * A MIUI bloqueia eventos de entrada injetados por ADB. A ponte usa apenas a
     * arvore de acessibilidade que o proprietario habilitou para o ROD e so
     * reconhece os rotulos oficiais desta etapa. Ela nunca aceita pagamento,
     * nunca escolhe restauracao e nunca digita telefone ou credencial.
     */
    private void setupWhatsAppCompanion(String request, int attempt, long deadline) {
        if (System.currentTimeMillis() >= deadline || attempt >= 45) {
            reply(request, false, null,
                "EQUATORIAL_WHATSAPP_SETUP_TIMEOUT: QR de aparelho adicional nao apareceu");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(WHATSAPP);
        if (root == null) {
            Intent launch = getPackageManager().getLaunchIntentForPackage(WHATSAPP);
            if (launch != null) startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            scheduleWhatsAppSetup(request, attempt + 1, deadline, 1600L);
            return;
        }

        if (containsAnyLabel(root, "escaneie o codigo qr", "escanear codigo qr",
                "scan this qr code", "vincular este aparelho")) {
            root.recycle();
            try {
                reply(request, true, new JSONObject().put("qr_ready", true)
                    .put("official_package", WHATSAPP), null);
            } catch (Exception error) {
                reply(request, false, null, "QR pronto, mas o estado nao pôde ser registrado");
            }
            return;
        }

        boolean advanced = false;
        if (containsAnyLabel(root, "bem-vindo", "welcome to whatsapp")) {
            advanced = gestureClickLabel(root, "concordar e continuar", "agree and continue");
        } else if (containsAnyLabel(root, "vincular como dispositivo adicional",
                "conectar como aparelho adicional", "link as companion device")) {
            advanced = gestureClickLabel(root, "vincular como dispositivo adicional",
                "conectar como aparelho adicional", "link as companion device");
        } else if (containsAnyLabel(root, "insira seu numero", "digite seu numero",
                "enter your phone number", "seu numero de telefone")) {
            advanced = gestureClickLabel(root, "mais opcoes", "more options");
        } else {
            // O menu de tres pontos pode ser a unica pista textual nesta versao.
            advanced = gestureClickLabel(root, "mais opcoes", "more options");
        }
        root.recycle();
        scheduleWhatsAppSetup(request, attempt + 1, deadline, advanced ? 1800L : 900L);
    }

    private void scheduleWhatsAppSetup(String request, int attempt, long deadline, long delay) {
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> setupWhatsAppCompanion(request, attempt, deadline), delay);
    }

    private boolean containsAnyLabel(AccessibilityNodeInfo root, String... labels) {
        for (String label : labels) if (containsLabel(root, label)) return true;
        return false;
    }

    /** Estado efêmero de uma consulta; os identificadores nunca tocam disco ou log. */
    private static final class ClaraRun {
        final String request, unit, document, birth;
        final long deadline;
        ClaraConversation.Stage stage = ClaraConversation.Stage.OPENING;
        final Map<String, Integer> actions = new HashMap<>();
        int polls;
        ClaraRun(String request, String unit, String document, String birth) {
            this.request = request; this.unit = unit; this.document = document;
            this.birth = birth == null ? "" : birth;
            this.deadline = System.currentTimeMillis() + 125_000L;
        }
    }

    private void startClaraConversation(String request, String unit, String document, String birth) {
        if (unit == null || unit.isEmpty() || document == null || document.isEmpty()) {
            reply(request, false, null, "EQUATORIAL_CREDENTIALS_MISSING: dados ausentes no cofre");
            return;
        }
        try {
            getPackageManager().getPackageInfo(WHATSAPP, 0);
            Intent chat = new Intent(Intent.ACTION_VIEW,
                Uri.parse("https://wa.me/556232432020")).setPackage(WHATSAPP)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            startActivity(chat);
            ClaraRun run = new ClaraRun(request, unit, document, birth);
            new Handler(Looper.getMainLooper()).postDelayed(() -> advanceClara(run), 3500L);
        } catch (Exception error) {
            reply(request, false, null, "EQUATORIAL_WHATSAPP_NOT_INSTALLED: canal oficial indisponivel");
        }
    }

    private void advanceClara(ClaraRun run) {
        if (System.currentTimeMillis() >= run.deadline) {
            reply(run.request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: Clara nao concluiu a consulta no prazo");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(WHATSAPP);
        if (root == null) {
            scheduleClara(run, 1000L);
            return;
        }
        List<String> values = new ArrayList<>();
        collect(root, values);
        String transcript = String.join("\n", values);
        ClaraConversation.Action action = ClaraConversation.decide(run.stage, transcript);

        try {
            if (action.kind == ClaraConversation.Kind.NOT_REGISTERED) {
                root.recycle();
                reply(run.request, false, null,
                    "EQUATORIAL_WHATSAPP_NOT_REGISTERED: vincule o Poco como aparelho adicional");
                return;
            }
            if (action.kind == ClaraConversation.Kind.FAILED) {
                root.recycle();
                reply(run.request, false, null,
                    "EQUATORIAL_CHANNEL_UNSUPPORTED: contato oficial nao abriu");
                return;
            }
            if (action.kind == ClaraConversation.Kind.NO_DEBT) {
                root.recycle();
                reply(run.request, true, new JSONObject()
                    .put("source", "clara_whatsapp").put("bill_count", 0)
                    .put("no_open_bills", true).put("debt_notice", "nenhuma fatura em aberto")
                    .put("amount", "").put("reference", "").put("due_date", "")
                    .put("barcode_present", false).put("pix_present", false), null);
                return;
            }
            if (action.kind == ClaraConversation.Kind.BILL) {
                EquatorialTextParser.Page page = EquatorialTextParser.parse(transcript);
                root.recycle();
                reply(run.request, true, new JSONObject()
                    .put("source", "clara_whatsapp").put("bill_count", 1)
                    .put("no_open_bills", false).put("amount", page.get("amount"))
                    .put("reference", page.get("reference")).put("due_date", page.get("due_date"))
                    .put("barcode_present", !page.get("barcode").isEmpty())
                    .put("pix_present", !page.get("pix").isEmpty()), null);
                return;
            }
            if (action.kind == ClaraConversation.Kind.CLICK) {
                boolean clicked = clickClaraChoice(root, action.value);
                root.recycle();
                if (clicked) {
                    run.stage = action.next; run.polls = 0;
                    scheduleClara(run, 2400L);
                } else {
                    scheduleClara(run, 900L);
                }
                return;
            }
            if (action.kind == ClaraConversation.Kind.MESSAGE) {
                String message = claraMessage(action.value, run);
                String key = action.value + ":" + transcript.hashCode();
                int repeats = run.actions.getOrDefault(action.value, 0);
                // O portal às vezes repete a pergunta após atualizar. Reenviar uma
                // vez é recuperação; uma terceira seria spam ou loop.
                if (repeats < 2 && !run.actions.containsKey(key)
                        && sendWhatsAppMessage(root, message)) {
                    run.actions.put(action.value, repeats + 1);
                    run.actions.put(key, 1);
                    run.stage = action.next; run.polls = 0;
                    root.recycle();
                    scheduleClara(run, 3000L);
                    return;
                }
            }
            root.recycle();
            run.polls++;
            // Se a saudação abriu um menu que a versão atual não expôs como
            // botão, peça o serviço por texto uma única vez.
            if (run.stage == ClaraConversation.Stage.STARTED && run.polls == 5) {
                AccessibilityNodeInfo fresh = packageRoot(WHATSAPP);
                if (fresh != null) {
                    sendWhatsAppMessage(fresh, "Consulta de débitos");
                    fresh.recycle();
                    run.stage = ClaraConversation.Stage.IDENTIFYING;
                }
            }
            scheduleClara(run, 1000L);
        } catch (Exception error) {
            try { root.recycle(); } catch (Exception ignored) { }
            reply(run.request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: conversa oficial ficou ilegivel");
        }
    }

    private void scheduleClara(ClaraRun run, long delay) {
        new Handler(Looper.getMainLooper()).postDelayed(() -> advanceClara(run), delay);
    }

    private static String claraMessage(String token, ClaraRun run) {
        if ("HELLO".equals(token)) return "Ola, quero consultar debitos de energia em Goias.";
        if ("UNIT".equals(token)) return run.unit;
        if ("DOCUMENT".equals(token)) return run.document;
        if ("BIRTH".equals(token)) return run.birth;
        if ("YES".equals(token)) return "Sim";
        return "";
    }

    private boolean sendWhatsAppMessage(AccessibilityNodeInfo root, String message) {
        if (message == null || message.isEmpty()) return false;
        List<AccessibilityNodeInfo> editors = new ArrayList<>();
        collectEditors(root, editors);
        if (editors.isEmpty()) return false;
        AccessibilityNodeInfo editor = editors.get(editors.size() - 1);
        setNodeText(editor, message);
        boolean clicked = gestureClickLabel(root, "enviar", "send");
        for (AccessibilityNodeInfo item : editors) item.recycle();
        return clicked;
    }

    private boolean clickClaraChoice(AccessibilityNodeInfo root, String choice) {
        if ("goias".equals(choice)) return gestureClickLabel(root, "goias", "goiás");
        if ("aceitar".equals(choice))
            return gestureClickLabel(root, "aceitar", "aceito", "concordar", "sim, aceito");
        if ("consulta de debitos".equals(choice))
            return gestureClickLabel(root, "consulta de debitos", "consulta de débitos",
                "consultar debitos", "consultar débitos", "segunda via");
        if ("continuar para a conversa".equals(choice))
            return gestureClickLabel(root, "continuar para a conversa", "continue to chat");
        return false;
    }

    private void bringSaneagoToFront(String request) {
        performGlobalAction(GLOBAL_ACTION_HOME);
        startActivity(new Intent(this, WakeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            try {
                Intent launch = getPackageManager().getLaunchIntentForPackage(SANEAGO);
                if (launch == null) throw new IllegalStateException("Saneago nao instalado");
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
                startActivity(launch);
                reply(request, true, new JSONObject().put("opened", true), null);
            } catch (Exception error) {
                reply(request, false, null, error.getClass().getSimpleName() + ": " + error.getMessage());
            }
        }, 1500);
    }

    private void loginSaneago(String request, String login, String password, int attempt) {
        if (login == null || password == null || login.isEmpty() || password.isEmpty()) {
            reply(request, false, null, "Credenciais Saneago ausentes no cofre");
            return;
        }
        AccessibilityNodeInfo root = saneagoRoot();
        if (root == null) root = packageRoot(CHROME);
        if (root == null) { reply(request, false, null, "Tela de login Saneago nao encontrada"); return; }
        List<AccessibilityNodeInfo> editors = new ArrayList<>();
        collectEditors(root, editors);
        if (editors.size() < 2) {
            boolean opened = gestureClickLabel(root, "faca login", "faça login", "abrir tela de login");
            root.recycle();
            if (attempt < 3) {
                if (!opened) {
                    Intent loginIntent = new Intent(Intent.ACTION_VIEW, Uri.parse("br.com.saneago://login"))
                        .setPackage(SANEAGO).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(loginIntent);
                }
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> loginSaneago(request, login, password, attempt + 1), 2200
                );
                return;
            }
            reply(request, false, null, "Campos de login Saneago nao estao acessiveis nesta tela");
            return;
        }
        setNodeText(editors.get(0), login);
        setNodeText(editors.get(1), password);
        boolean clicked = gestureClickLabel(root, "entrar", "acessar");
        if (!clicked) clicked = clickFirst(root, "entrar", "acessar", "login");
        for (AccessibilityNodeInfo editor : editors) editor.recycle();
        root.recycle();
        reply(request, clicked, new JSONObject(), clicked ? null : "Botao de entrada Saneago nao encontrado");
    }

    private void selectSaneago(String request, String account, int step) {
        String expected = digits(account);
        if (expected.isEmpty()) { reply(request, false, null, "Conta Saneago ausente"); return; }
        AccessibilityNodeInfo root = saneagoRoot();
        if (root == null) { reply(request, false, null, "Tela Saneago nao encontrada"); return; }
        if (containsDigits(root, expected)) {
            boolean current = step == 0 && containsLabel(root, "fatura atual");
            if (current) {
                root.recycle();
                reply(request, true, new JSONObject(), null);
                return;
            }
            boolean clicked = gestureClickDigits(root, expected);
            root.recycle();
            if (clicked) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> confirmSaneagoSelection(request), 900);
            } else {
                reply(request, false, null, "Conta Saneago encontrada, mas nao selecionavel");
            }
            return;
        }
        boolean advanced = false;
        boolean returnHome = false;
        if (step == 0) {
            advanced = gestureClickLabel(root, "conta:");
            if (!advanced && containsLabel(root, "agência virtual")) {
                returnHome = gestureClickLabel(root, "home");
            }
        }
        else if (step == 1) advanced = gestureClickLabel(root, "trocar conta", "minhas contas", "contas");
        root.recycle();
        if (returnHome) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> selectSaneago(request, account, 0), 1800
            );
            return;
        }
        if (advanced && step < 2) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> selectSaneago(request, account, step + 1), 1800
            );
            return;
        }
        reply(request, false, null, "Conta Saneago nao apareceu no seletor");
    }

    private void confirmSaneagoSelection(String request) {
        AccessibilityNodeInfo root = saneagoRoot();
        if (root == null) { reply(request, false, null, "Confirmacao da conta Saneago nao encontrada"); return; }
        boolean clicked = gestureClickLabel(root, "ok", "confirmar");
        root.recycle();
        if (clicked) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> reply(request, true, new JSONObject(), null), 1800);
        } else {
            reply(request, false, null, "Botao OK da conta Saneago nao encontrado");
        }
    }

    /**
     * Fecha o aviso de privacidade e confirma que ele saiu.
     *
     * Fechar e clicar no mesmo passo nao funcionava: o clique em "Acessar" era
     * reportado como sucesso mas o modal ainda cobria a pagina, entao nada
     * avancava. O fechamento agora e um passo proprio, com verificacao.
     */
    private void dismissEquatorialOverlay(String request) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) { reply(request, false, null, "Portal Equatorial nao encontrado no Chrome"); return; }
        if (!hasOverlay(root)) {
            RodLog.step("aviso", "nao havia aviso sobreposto");
            root.recycle(); reply(request, true, new JSONObject(), null); return;
        }
        RodLog.step("aviso", "aviso presente, tentando fechar");
        // Gesto primeiro. Em conteudo web, ACTION_CLICK devolve true mesmo quando a
        // pagina nao reage, o que escondia a falha e impedia a segunda tentativa.
        // E o mesmo motivo pelo qual o login da Saneago ja tenta o gesto antes.
        if (!gestureClickLabel(root, "fechar")) clickFirst(root, "fechar");
        root.recycle();
        new Handler(Looper.getMainLooper()).postDelayed(() -> retireOverlay(request, 0), 1500);
    }

    private void retireOverlay(String request, int attempt) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) { reply(request, false, null, "Portal Equatorial nao encontrado no Chrome"); return; }
        if (!hasOverlay(root)) {
            RodLog.step("aviso", "fechado na tentativa " + attempt);
            root.recycle(); reply(request, true, new JSONObject(), null); return;
        }
        RodLog.step("aviso", "ainda presente na tentativa " + attempt);
        if (attempt >= 2) {
            root.recycle();
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: o aviso de privacidade do portal nao fechou");
            return;
        }
        if (attempt == 0) {
            if (!gestureClickLabel(root, "fechar")) clickFirst(root, "fechar");
        } else if (systemDialogInFront()) {
            // BACK so e seguro contra dialogo do sistema. Em conteudo web ele navega
            // o Chrome para tras: na volta hasOverlay e falso e o passo respondia
            // sucesso estando em OUTRA pagina, sem aviso nenhum.
            RodLog.step("aviso", "aviso e dialogo do sistema, fechando com voltar");
            performGlobalAction(GLOBAL_ACTION_BACK);
        } else {
            RodLog.step("aviso", "aviso e conteudo web, repetindo o toque em fechar");
            if (!gestureClickLabel(root, "fechar", "close")) clickFirst(root, "fechar");
        }
        root.recycle();
        new Handler(Looper.getMainLooper()).postDelayed(() -> retireOverlay(request, attempt + 1), 1500);
    }

    private static boolean hasOverlay(AccessibilityNodeInfo root) {
        return containsLabel(root, "aviso de privacidade") || containsLabel(root, "seus direitos garantidos");
    }

    // ------------------------------------------------------ sessao do portal

    /** Campos de autenticacao do portal. Ids estaveis, conferidos no aparelho. */
    private static final String LOGIN_UNIT_FIELD = "WEBDOOR_headercorporativogo_txtUC";
    private static final String LOGIN_DOCUMENT_FIELD = "WEBDOOR_headercorporativogo_txtDocumento";
    /** Marcador estrutural da segunda via autenticada. */
    private static final String BILL_SELECTOR_FIELD = "CONTENT_comboBoxUC";
    /** Prazo para o portal responder ao envio de credenciais. */
    private static final long LOGIN_SETTLE_MILLIS = 25_000L;
    /** Conferencias do formulario antes de desistir de preenche-lo. */
    private static final int LOGIN_FILL_ATTEMPTS = 4;
    /** Sair da Agencia Virtual. Encerra a sessao do portal, e nada mais que isso. */
    private static final String LOGOUT_URL =
        "https://goias.equatorialenergia.com.br/AgenciaGO/Servi%C3%A7os/comum/Sair.aspx";
    /**
     * Tela de autenticacao do portal.
     *
     * Sem sessao valida a rota da segunda via nao mostra o login: ela estoura no
     * servidor e cai na pagina de suporte. Entao o passo de login navega ele mesmo
     * ate aqui em vez de esperar que o formulario apareca sozinho.
     */
    private static final String LOGIN_URL = "https://goias.equatorialenergia.com.br/LoginGO.aspx";

    /** Site novo: login UC+CPF e funil que lista o débito antes de qualquer pagamento. */
    private static final String GO_ACCOUNT_URL = AgenciaWebLogin.LOGIN_URL;
    private static final String GO_DEBTS_URL =
        "https://go.equatorialenergia.com.br" + AgenciaWebLogin.DEBTS_PATH;
    private static final String GO_DOCUMENT_FIELD = "identificador";
    private static final String GO_UNIT_FIELD = "senha-identificador";
    private static final int GO_LOGIN_SUBMISSIONS = 5;
    private static final long GO_FLOW_MILLIS = 56_000L;
    /** Canal oficial de titular, autenticado por CPF+nascimento, sem SMS. */
    private static final String HOLDER_LOGIN_URL =
        "https://energiaemdia.equatorialenergia.com.br/login";
    private static final long HOLDER_FLOW_MILLIS = 56_000L;

    /**
     * Descobre a correspondencia de uma UC legada pelo canal oficial do titular.
     *
     * Este passo existe somente para a migracao nacional de identificadores: ele
     * nao consulta endpoint escondido e nao injeta JavaScript. O ROD preenche os
     * controles visiveis do Chrome, escolhe Goias no select nativo e observa a
     * pagina que a Equatorial devolve. Credenciais nunca entram na trilha.
     */
    private void discoverHolderUnits(String request, String legacyUnit) {
        BillingConfig config = BillingConfig.load(getApplicationContext());
        String document = config.value("equatorial_cpf").replaceAll("\\D", "");
        String birth = config.value("equatorial_birth_date").trim();
        String legacy = legacyUnit == null ? "" : legacyUnit.replaceAll("\\D", "");
        if (document.length() != 11 || !birth.matches("\\d{2}/\\d{2}/\\d{4}")
                || legacy.isEmpty()) {
            reply(request, false, null,
                "EQUATORIAL_AUTH_REQUIRED: identificacao do titular incompleta no cofre");
            return;
        }
        RodLog.step("titular", "abrindo canal oficial CPF+nascimento para mapear UC legada");
        long deadline = System.currentTimeMillis() + HOLDER_FLOW_MILLIS;
        openRoute(HOLDER_LOGIN_URL);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> holderLoginStep(request, legacy, document, birth, deadline, 0), 2200);
    }

    private void holderLoginStep(String request, String legacy, String document,
                                 String birth, long deadline, int poll) {
        if (System.currentTimeMillis() >= deadline) {
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: canal oficial do titular nao concluiu o login");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, poll + 1), 700);
            return;
        }
        if (containsLabel(root, "nos usamos cookies") || containsLabel(root, "cookies")) {
            boolean closed = gestureClickExact(root, "rejeitar");
            if (!closed) closed = clickFirst(root, "rejeitar");
            root.recycle();
            RodLog.step("titular", "aviso de cookies fechado=" + closed);
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, poll + 1), 900);
            return;
        }
        List<AccessibilityNodeInfo> editors = new ArrayList<>();
        collectWebEditors(root, editors);
        if (editors.size() < 2) {
            for (AccessibilityNodeInfo editor : editors) editor.recycle();
            root.recycle();
            if (poll > 2) {
                holderInspect(request, legacy, deadline, 0);
                return;
            }
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, poll + 1), 700);
            return;
        }
        setNodeText(editors.get(0), document);
        setNodeText(editors.get(1), birth);
        for (AccessibilityNodeInfo editor : editors) editor.recycle();
        AccessibilityNodeInfo state = findByViewId(root, "state");
        boolean stateReady = state != null
            && EquatorialSession.fold(String.valueOf(state.getText())).contains("goias");
        Rect stateBounds = new Rect();
        if (state != null) state.getBoundsInScreen(stateBounds);
        if (state != null) state.recycle();
        root.recycle();
        RodLog.step("titular", "campos preenchidos; estado_pronto=" + stateReady
            + " seletor_visivel=" + !stateBounds.isEmpty());
        if (stateReady) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderSubmit(request, legacy, document, birth, deadline), 500);
        } else if (!stateBounds.isEmpty()) {
            Path path = new Path();
            path.moveTo(stateBounds.exactCenterX(), stateBounds.exactCenterY());
            GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, 140)).build();
            boolean scheduled = dispatchGesture(gesture, new GestureResultCallback() {
                @Override public void onCompleted(GestureDescription description) {
                    RodLog.step("titular", "toque fisico no seletor concluido");
                    new Handler(Looper.getMainLooper()).postDelayed(
                        () -> holderChooseGoias(request, legacy, document, birth, deadline, 0), 500);
                }
                @Override public void onCancelled(GestureDescription description) {
                    holderLoginStep(request, legacy, document, birth, deadline, poll + 1);
                }
            }, null);
            if (!scheduled) holderLoginStep(request, legacy, document, birth, deadline, poll + 1);
        } else {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, poll + 1), 700);
        }
    }

    private void holderChooseGoias(String request, String legacy, String document,
                                   String birth, long deadline, int poll) {
        AccessibilityNodeInfo dialog = contractDialogRoot();
        if (dialog == null) {
            if (poll < 10 && System.currentTimeMillis() < deadline) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> holderChooseGoias(request, legacy, document, birth, deadline, poll + 1), 400);
                return;
            }
            holderLoginStep(request, legacy, document, birth, deadline, poll + 1);
            return;
        }
        boolean selected = gestureClickExact(dialog, "goias");
        if (!selected) selected = clickFirst(dialog, "goiás", "goias");
        dialog.recycle();
        RodLog.step("titular", "Goias selecionado=" + selected);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> holderSubmit(request, legacy, document, birth, deadline), 900);
    }

    private void holderSubmit(String request, String legacy, String document,
                              String birth, long deadline) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, 0), 600);
            return;
        }
        List<AccessibilityNodeInfo> editors = new ArrayList<>();
        collectWebEditors(root, editors);
        boolean ready = editors.size() >= 2
            && filled(editors.get(0), document) && filled(editors.get(1), birth);
        for (AccessibilityNodeInfo editor : editors) editor.recycle();
        boolean clicked = ready && gestureClickExact(root, "continuar");
        if (!clicked && ready) clicked = clickFirst(root, "continuar");
        root.recycle();
        RodLog.step("titular", "CONTINUAR acionado=" + clicked);
        if (!clicked) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderLoginStep(request, legacy, document, birth, deadline, 0), 700);
            return;
        }
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> holderInspect(request, legacy, deadline, 0), 8000);
    }

    private void holderInspect(String request, String legacy, long deadline, int poll) {
        if (System.currentTimeMillis() >= deadline) {
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: canal do titular nao mostrou as unidades");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> holderInspect(request, legacy, deadline, poll + 1), 700);
            return;
        }
        List<AccessibilityNodeInfo> editors = new ArrayList<>();
        collectWebEditors(root, editors);
        boolean loginStillVisible = editors.size() >= 2 && containsLabel(root, "entre na sua conta");
        for (AccessibilityNodeInfo editor : editors) editor.recycle();
        List<String> values = new ArrayList<>();
        collect(root, values);
        String text = String.join("\n", values);
        root.recycle();
        if (loginStillVisible) {
            if (poll < 8) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> holderInspect(request, legacy, deadline, poll + 1), 900);
                return;
            }
            reply(request, false, null,
                "EQUATORIAL_AUTH_REQUIRED: canal oficial do titular recusou CPF, nascimento ou estado");
            return;
        }
        try {
            reply(request, true, new JSONObject()
                .put("authenticated", true)
                .put("legacy_visible", ContractMatch.matches(text, legacy))
                .put("page_items", values.size()), null);
        } catch (Exception error) {
            reply(request, false, null,
                "EQUATORIAL_MAPPING_NOT_FOUND: pagina autenticada ficou ilegivel");
        }
    }

    /**
     * Consulta pelo site novo dentro do Chrome real.
     *
     * O WebView chegou ao funil, mas o reCAPTCHA por pontuação não emitiu uma
     * requisição. Este caminho usa o Chrome normal do Poco, preserva o desafio e
     * só aciona controles visíveis. Recarregar o formulário não é considerado
     * imediatamente uma recusa: o portal às vezes redesenha os dois campos e
     * apaga um deles enquanto calcula risco, então cada rodada confere os dois,
     * reaplica somente o ausente e tem teto absoluto.
     */
    private void readDebtsChromeGo(final String request, final String property,
                                   final String mappedUnit) {
        final String imovel = property == null || property.trim().isEmpty()
            ? "casa" : property.trim();
        final String unit;
        final String document;
        try {
            BillingConfig config = BillingConfig.load(getApplicationContext());
            String configured = mappedUnit == null || mappedUnit.trim().isEmpty()
                ? config.value(imovel + "_energy") : mappedUnit;
            unit = AgenciaWebLogin.unit(configured);
            document = AgenciaWebLogin.document(config.value("equatorial_cpf"));
            if (!AgenciaWebLogin.ready(document, unit))
                throw new IllegalStateException(
                    "EQUATORIAL_AUTH_REQUIRED: credenciais ausentes no cofre");
        } catch (Exception error) {
            reply(request, false, null, error.getMessage());
            return;
        }
        RodLog.step("chrome-go", "imovel=" + imovel
            + " documento=" + RodLog.describe(document)
            + " unidade=" + RodLog.describe(unit));
        // Primeiro mede a sessão existente. Isso evita gastar um login quando o
        // job seguinte pede o mesmo imóvel e permite sair explicitamente quando
        // a sessão pertence a outra UC.
        long deadline = System.currentTimeMillis() + GO_FLOW_MILLIS;
        AccessibilityNodeInfo active = packageRoot(CHROME);
        if (active != null) {
            List<String> currentValues = new ArrayList<>();
            collect(active, currentValues);
            String currentText = String.join("\n", currentValues).replaceAll("\\s", "");
            boolean sameUnit = currentText.contains(unit);
            boolean currentFunnel = containsLabel(active, "pagamento de faturas on line")
                || containsLabel(active, "pagamento de faturas online");
            AccessibilityNodeInfo loginDocument = findByViewId(active, GO_DOCUMENT_FIELD);
            boolean loginForm = loginDocument != null;
            if (loginDocument != null) loginDocument.recycle();
            active.recycle();
            if (sameUnit && currentFunnel && !loginForm) {
                RodLog.step("chrome-go", "reutilizando guia estabilizada do imovel pedido");
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, false, 0), 500);
                return;
            }
        }
        openRoute(GO_DEBTS_URL);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> chromeGoInitial(request, unit, document, deadline, 0), 2200);
    }

    private void chromeGoInitial(String request, String unit, String document,
                                 long deadline, int poll) {
        if (System.currentTimeMillis() >= deadline) {
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: o site novo nao mostrou um estado inicial");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoInitial(request, unit, document, deadline, poll + 1), 800);
            return;
        }
        List<String> values = new ArrayList<>();
        collect(root, values);
        String text = String.join("\n", values).replaceAll("\\s", "");
        boolean requestedUnit = text.contains(unit);
        boolean funnel = containsLabel(root, "pagamento de faturas on line")
            || containsLabel(root, "pagamento de faturas online");
        AccessibilityNodeInfo doc = findByViewId(root, GO_DOCUMENT_FIELD);
        AccessibilityNodeInfo uc = findByViewId(root, GO_UNIT_FIELD);
        boolean login = doc != null && uc != null;
        if (requestedUnit && funnel && !login) {
            if (doc != null) doc.recycle();
            if (uc != null) uc.recycle();
            root.recycle();
            RodLog.step("chrome-go", "sessao existente pertence ao imovel pedido");
            chromeGoDebtStep(request, unit, deadline, false, 0);
            return;
        }
        if (doc != null) doc.recycle();
        if (uc != null) uc.recycle();
        if (login) {
            root.recycle();
            chromeGoLogin(request, unit, document, deadline, 0, 0);
            return;
        }
        if (containsLabel(root, "sair")) {
            boolean clicked = gestureClickExact(root, "sair");
            if (!clicked) clicked = gestureClickLabel(root, "sair");
            if (!clicked) clicked = clickFirst(root, "sair");
            root.recycle();
            RodLog.step("chrome-go", "sessao era de outra unidade; SAIR acionado=" + clicked);
            new Handler(Looper.getMainLooper()).postDelayed(() -> {
                openRoute(GO_ACCOUNT_URL);
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoLogin(request, unit, document, deadline, 0, 0), 1800);
            }, 1400);
            return;
        }
        if (hasOverlay(root)) gestureClickLabel(root, "fechar");
        root.recycle();
        if (poll == 3) openRoute(GO_ACCOUNT_URL);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> chromeGoInitial(request, unit, document, deadline, poll + 1), 900);
    }

    private void chromeGoLogin(String request, String unit, String document,
                               long deadline, int poll, int submissions) {
        if (System.currentTimeMillis() >= deadline) {
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: o site novo nao concluiu o login no Chrome");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoLogin(request, unit, document, deadline, poll + 1, submissions),
                UI_POLL_MILLIS * 2);
            return;
        }
        if (hasOverlay(root)) {
            if (submissions > 0) {
                // Depois do login o site abre um cadastro LGPD opcional com
                // nome/e-mail/telefone e desabilita Fechar. O JWT já existe —
                // confirmado no Chrome real ao abrir o funil — então atravessar
                // pela própria rota oficial evita inventar dados ou consentir.
                root.recycle();
                RodLog.step("chrome-go", "painel LGPD pos-login; seguindo sem consentir");
                openRoute(GO_DEBTS_URL);
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, false, 0), 2500);
                return;
            }
            // Fechar não concede o consentimento do botão Enviar.
            gestureClickLabel(root, "fechar");
            root.recycle();
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoLogin(request, unit, document, deadline, poll + 1, submissions), 1200);
            return;
        }
        AccessibilityNodeInfo docField = findByViewId(root, GO_DOCUMENT_FIELD);
        AccessibilityNodeInfo unitField = findByViewId(root, GO_UNIT_FIELD);
        boolean form = docField != null && unitField != null;
        if (!form) {
            if (docField != null) docField.recycle();
            if (unitField != null) unitField.recycle();
            // Antes do primeiro envio, ausência é só carregamento. Depois dele,
            // desaparecer é o marcador observável de sessão aceita.
            boolean rejected = containsLabel(root, AgenciaWebLogin.GENERIC_FAILURE_CODE)
                || containsLabel(root, "não foi possível realizar seu login")
                || containsLabel(root, "nao foi possivel realizar seu login");
            root.recycle();
            if (rejected) {
                reply(request, false, null,
                    "EQUATORIAL_AUTH_REQUIRED: a Agencia Web recusou o acesso no Chrome");
                return;
            }
            if (submissions > 0 && poll >= 2) {
                RodLog.step("chrome-go", "formulario saiu da tela; abrindo lista de debitos");
                openRoute(GO_DEBTS_URL);
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, false, 0), 2500);
                return;
            }
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoLogin(request, unit, document, deadline, poll + 1, submissions),
                UI_POLL_MILLIS * 2);
            return;
        }
        if (submissions >= GO_LOGIN_SUBMISSIONS) {
            docField.recycle(); unitField.recycle(); root.recycle();
            reply(request, false, null,
                "EQUATORIAL_AUTH_REQUIRED: o formulario do site novo foi recarregado cinco vezes");
            return;
        }
        boolean docOk = filled(docField, document);
        boolean unitOk = filled(unitField, unit);
        if (!docOk) setNodeText(docField, document);
        if (!unitOk) setNodeText(unitField, unit);
        docField.recycle(); unitField.recycle(); root.recycle();
        RodLog.step("chrome-go", "rodada=" + submissions
            + " documento_ok=" + docOk + " unidade_ok=" + unitOk);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> chromeGoSubmit(request, unit, document, deadline, submissions), 700);
    }

    private void chromeGoSubmit(String request, String unit, String document,
                                long deadline, int submissions) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoLogin(request, unit, document, deadline, 0, submissions), 800);
            return;
        }
        AccessibilityNodeInfo docField = findByViewId(root, GO_DOCUMENT_FIELD);
        AccessibilityNodeInfo unitField = findByViewId(root, GO_UNIT_FIELD);
        boolean ready = docField != null && unitField != null
            && filled(docField, document) && filled(unitField, unit);
        if (docField != null) docField.recycle();
        if (unitField != null) unitField.recycle();
        boolean clicked = ready && gestureClickExact(root, "acessar");
        if (!clicked && ready) clicked = gestureClickLabel(root, "acessar");
        root.recycle();
        RodLog.step("chrome-go", "ACESSAR acionado=" + clicked);
        if (!clicked) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoLogin(request, unit, document, deadline, 0, submissions), 800);
            return;
        }
        // O reCAPTCHA executa de forma assíncrona. Oito segundos evita reenviar
        // enquanto a tentativa legítima ainda está sendo julgada.
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> chromeGoLogin(request, unit, document, deadline, 0, submissions + 1), 8000);
    }

    private void chromeGoDebtStep(String request, String unit, long deadline,
                                  boolean advanced, int poll) {
        if (System.currentTimeMillis() >= deadline) {
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: o funil oficial nao entregou a fatura");
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoDebtStep(request, unit, deadline, advanced, poll + 1), 800);
            return;
        }
        if (hasOverlay(root)) {
            gestureClickLabel(root, "fechar");
            root.recycle();
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoDebtStep(request, unit, deadline, advanced, poll + 1), 1200);
            return;
        }
        AccessibilityNodeInfo loginField = findByViewId(root, GO_DOCUMENT_FIELD);
        if (loginField != null) {
            loginField.recycle();
            root.recycle();
            reply(request, false, null,
                "EQUATORIAL_AUTH_REQUIRED: a sessao do site novo nao chegou ao funil");
            return;
        }
        List<String> values = new ArrayList<>();
        collect(root, values);
        String text = String.join("\n", values);
        if (!text.replaceAll("\\s", "").contains(unit)) {
            root.recycle();
            reply(request, false, null,
                "EQUATORIAL_CONTRACT_NOT_FOUND: o funil abriu uma unidade diferente da pedida");
            return;
        }
        String notice = AgenciaWebLogin.debtNotice(EquatorialSession.fold(text));
        AccessibilityNodeInfo chooserButton = findByViewId(root, "submit-payment-bemobi");
        boolean chooserStillVisible = chooserButton != null;
        if (chooserButton != null) chooserButton.recycle();
        try {
            EquatorialTextParser.Page page = EquatorialTextParser.parse(text);
            boolean amount = !page.get("amount").isEmpty();
            boolean reference = !page.get("reference").isEmpty();
            // A página de escolha tem textos institucionais no rodapé que o
            // vocabulário de "conta em dia" pode reconhecer fora de contexto.
            // Antes de Continuar, somente valor+referência é desfecho; aviso de
            // ausência só vale na tela que o servidor devolve depois do envio.
            if ((amount && reference)
                || (advanced && !chooserStillVisible && !notice.isEmpty())) {
                JSONObject result = new JSONObject()
                    .put("amount", page.get("amount"))
                    .put("reference", page.get("reference"))
                    .put("due_date", page.get("due_date"))
                    .put("no_open_bills",
                        advanced && !chooserStillVisible && !notice.isEmpty())
                    .put("debt_notice", notice)
                    .put("bill_count", amount && reference ? 1 : 0)
                    .put("read_provider", AgenciaWebLogin.ReadProvider.READ_PROVIDER_OK.name())
                    .put("read_only", true).put("barcode_present", false)
                    .put("pix_present", false).put("chrome_real", true);
                root.recycle();
                RodLog.step("chrome-go", "consulta concluida valor=" + amount
                    + " referencia=" + reference + " sem_debito="
                    + (advanced && !chooserStillVisible && !notice.isEmpty()));
                reply(request, true, result, null);
                return;
            }
        } catch (Exception parseError) {
            root.recycle();
            reply(request, false, null,
                "EQUATORIAL_BILL_NOT_FOUND: a tela oficial nao ficou legivel");
            return;
        }
        if (!advanced) {
            AccessibilityNodeInfo button = findByViewId(root, "submit-payment-bemobi");
            Rect bounds = new Rect();
            if (button != null) {
                button.getBoundsInScreen(bounds);
                int width = getResources().getDisplayMetrics().widthPixels;
                int height = getResources().getDisplayMetrics().heightPixels;
                boolean onScreen = !bounds.isEmpty()
                    && bounds.exactCenterX() >= 0 && bounds.exactCenterX() < width
                    && bounds.exactCenterY() >= 0 && bounds.exactCenterY() < height;
                RodLog.step("chrome-go", "CONTINUAR geometria="
                    + bounds.width() + "x" + bounds.height() + " na_tela=" + onScreen);
                if (!onScreen) {
                    // ACTION_CLICK em nó web fora da dobra devolve true sem
                    // disparar evento. Primeiro rola o WebView e volta a medir;
                    // o gesto só é permitido quando o nó tem coordenada real.
                    boolean scrolled = scrollForward(root);
                    if (!scrolled) scrolled = gestureScrollUp();
                    button.recycle();
                    root.recycle();
                    RodLog.step("chrome-go", "CONTINUAR fora da dobra; rolou=" + scrolled);
                    if (scrolled && poll < 8) {
                        new Handler(Looper.getMainLooper()).postDelayed(
                            () -> chromeGoDebtStep(request, unit, deadline, false, poll + 1), 900);
                        return;
                    }
                    reply(request, false, null,
                        "EQUATORIAL_PORTAL_TIMEOUT: o Chrome nao revelou o botao Continuar");
                    return;
                }
            }
            if (button == null || bounds.isEmpty()) {
                if (button != null) button.recycle();
                root.recycle();
                reply(request, false, null,
                    "EQUATORIAL_BILL_NOT_FOUND: o funil oficial nao exibiu Continuar");
                return;
            }
            // Em conteúdo web o ACTION_CLICK preserva a ativação semântica do
            // botão (incluindo submit e reCAPTCHA). O gesto por coordenada é
            // apenas fallback: ele pode terminar no Android sem que o DOM
            // receba um evento, por exemplo quando o Chrome reposiciona a
            // viewport entre a leitura do nó e a injeção do toque.
            boolean semanticClick = button.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            button.recycle();
            root.recycle();
            if (semanticClick) {
                RodLog.step("chrome-go", "CONTINUAR acao semantica aceita pelo Chrome");
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> verifySemanticContinue(request, unit, deadline), 1400);
            } else {
                tapContinue(request, unit, deadline, bounds, 0);
            }
            return;
        } else {
            if (chooserStillVisible && poll == 5) {
                AccessibilityNodeInfo retry = findByViewId(root, "submit-payment-bemobi");
                Rect retryBounds = new Rect();
                if (retry != null) {
                    retry.getBoundsInScreen(retryBounds);
                    retry.recycle();
                }
                root.recycle();
                if (!retryBounds.isEmpty()) {
                    RodLog.step("chrome-go", "pagina nao avancou; repetindo toque fisico uma vez");
                    tapContinue(request, unit, deadline, retryBounds, 6);
                    return;
                }
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, true, poll + 1), 900);
                return;
            }
            root.recycle();
            if (poll < 16) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, true, poll + 1), 900);
                return;
            }
        }
        reply(request, false, null,
            "EQUATORIAL_BILL_NOT_FOUND: o funil oficial nao exibiu debito nem estado em dia");
    }

    /**
     * ACTION_CLICK pode devolver true para um nó HTML sem entregar o evento ao
     * DOM do Chrome. Antes do fallback físico, confirmamos que o mesmo botão
     * ainda está presente; assim não há segundo toque depois de uma navegação.
     */
    private void verifySemanticContinue(String request, String unit, long deadline) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> chromeGoDebtStep(request, unit, deadline, true, 0), 1200);
            return;
        }
        AccessibilityNodeInfo button = findByViewId(root, "submit-payment-bemobi");
        if (button == null) {
            root.recycle();
            RodLog.step("chrome-go", "CONTINUAR saiu da pagina apos acao semantica");
            chromeGoDebtStep(request, unit, deadline, true, 0);
            return;
        }
        Rect bounds = new Rect();
        button.getBoundsInScreen(bounds);
        button.recycle();
        root.recycle();
        RodLog.step("chrome-go", "acao semantica nao mudou a pagina; usando toque fisico");
        tapContinue(request, unit, deadline, bounds, 0);
    }

    /** Rola o primeiro contêiner acessível; não injeta gesto nem usa coordenada. */
    private boolean scrollForward(AccessibilityNodeInfo node) {
        if (node.isScrollable()
            && node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)) return true;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean scrolled = scrollForward(child);
                child.recycle();
                if (scrolled) return true;
            }
        }
        return false;
    }

    /** Rolagem física pelo serviço, usada só quando o WebView recusa ACTION_SCROLL. */
    private boolean gestureScrollUp() {
        int width = getResources().getDisplayMetrics().widthPixels;
        int height = getResources().getDisplayMetrics().heightPixels;
        Path path = new Path();
        path.moveTo(width / 2f, height * 0.78f);
        path.lineTo(width / 2f, height * 0.34f);
        return dispatchGesture(new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 450)).build(), null, null);
    }

    /**
     * Toca Continuar e só chama de toque depois do callback do Android.
     *
     * {@code dispatchGesture} devolver true significa apenas "agendado". A
     * versão anterior tratava isso como clique consumado e esperava uma resposta
     * de rede para um evento que podia ter sido cancelado pelo sistema.
     */
    private void tapContinue(String request, String unit, long deadline,
                             Rect bounds, int nextPoll) {
        Path path = new Path();
        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
        GestureDescription gesture = new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 140)).build();
        boolean scheduled = dispatchGesture(gesture, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription description) {
                RodLog.step("chrome-go", "CONTINUAR gesto concluido pelo Android");
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> chromeGoDebtStep(request, unit, deadline, true, nextPoll), 6500);
            }
            @Override public void onCancelled(GestureDescription description) {
                RodLog.fail("chrome-go", "CONTINUAR gesto cancelado pelo Android");
                reply(request, false, null,
                    "EQUATORIAL_PORTAL_TIMEOUT: o Android cancelou o toque em Continuar");
            }
        }, null);
        if (!scheduled) reply(request, false, null,
            "EQUATORIAL_PORTAL_TIMEOUT: o Android recusou agendar o toque em Continuar");
    }

    /**
     * Diz em que estado a sessao esta, sem tratar expiracao como falha.
     *
     * Este passo nunca responde erro por sessao caida: sessao caida e um estado do
     * caminho, e quem decide o que fazer com ele e a maquina de estados no leitor.
     * O passo so espera por um marcador ESTRUTURAL — o seletor de unidade ou os
     * dois campos de login — porque texto de rodape fala de login em toda pagina
     * do portal, e decidir por texto ja reportou sessao expirada com sessao viva.
     */
    /**
     * Sonda o aplicativo oficial: ele já tem sessão viva?
     *
     * Essa é a única pergunta, e ela vale muito: os dois portais web recusaram o
     * login automatizado por antifraude, e sessão viva no aplicativo dispensaria
     * login inteiramente. Então aqui se ABRE e se OLHA — nada é preenchido, nada
     * é acionado, nenhuma credencial atravessa esta função.
     *
     * O que sai daqui é contagem e presença de marcador, nunca conteúdo: o nome
     * do titular e a unidade consumidora aparecem na tela logada, e não é papel
     * de uma sondagem carregá-los para fora do aparelho. Por isso a resposta é um
     * punhado de booleanos, e a trilha registra a mesma coisa.
     */
    private void probeEquatorialApp(final String request) {
        performGlobalAction(GLOBAL_ACTION_HOME);
        startActivity(new Intent(this, WakeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            try {
                Intent launch = getPackageManager().getLaunchIntentForPackage(EQUATORIAL_APP);
                if (launch == null) throw new IllegalStateException(
                    "EQUATORIAL_CHANNEL_NOT_INSTALLED: aplicativo oficial ausente");
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
                startActivity(launch);
            } catch (Exception error) {
                reply(request, false, null, error.getClass().getSimpleName() + ": " + error.getMessage());
                return;
            }
            // O aplicativo pinta a primeira tela depois de resolver sessão com o
            // servidor; olhar cedo confundiria "carregando" com "sem sessão".
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> readEquatorialAppMarkers(request, 0), 9000);
        }, 1500);
    }

    /**
     * Lê marcador estrutural da tela do aplicativo oficial, com nova tentativa.
     *
     * A árvore pode vir vazia enquanto a primeira tela ainda monta, e uma leitura
     * única transformaria demora em veredito. Tenta de novo algumas vezes antes de
     * concluir que o aplicativo não entregou tela nenhuma.
     */
    private void readEquatorialAppMarkers(final String request, final int attempt) {
        AccessibilityNodeInfo root = packageRoot(EQUATORIAL_APP);
        if (root == null) {
            if (attempt < 4) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> readEquatorialAppMarkers(request, attempt + 1), 4000);
                return;
            }
            RodLog.fail("appoficial", "o aplicativo nao entregou tela nenhuma");
            reply(request, false, null,
                "EQUATORIAL_PORTAL_TIMEOUT: o aplicativo oficial nao pintou tela legivel");
            return;
        }
        List<String> values = new ArrayList<>();
        try {
            collect(root, values);
        } finally {
            root.recycle();
        }
        StringBuilder joined = new StringBuilder();
        for (String value : values) joined.append(value).append('\n');
        String lower = joined.toString().toLowerCase();

        boolean login = lower.contains("entrar") || lower.contains("acessar")
            || lower.contains("senha") || lower.contains("cadastr");
        boolean segundaVia = lower.contains("segunda via") || lower.contains("2ª via")
            || lower.contains("2a via");
        boolean faturas = lower.contains("fatura") || lower.contains("debito")
            || lower.contains("débito");
        boolean pix = lower.contains("pix");
        boolean barcode = lower.contains("codigo de barras") || lower.contains("código de barras");
        try {
            RodLog.step("appoficial", "nos=" + values.size() + " login=" + login
                + " segundavia=" + segundaVia + " faturas=" + faturas
                + " pix=" + pix + " barras=" + barcode);
            reply(request, true, new JSONObject()
                .put("nodes", values.size()).put("login", login)
                .put("segunda_via", segundaVia).put("faturas", faturas)
                .put("pix", pix).put("barcode", barcode), null);
        } catch (Exception error) {
            reply(request, false, null, "JSONException: " + error.getMessage());
        }
    }

    /**
     * Autentica na Agência Web (host {@code go.*}) pelo motor WebView próprio.
     *
     * É o SEGUNDO portal da concessionária, e o único cuja porta o ROD ainda não
     * sabe se atravessa: o ASPX do host {@code goias.*} é guardado pelo Transmit
     * Security DRS, que recusou a automação em silêncio, e aqui o portão é
     * reCAPTCHA v3, por pontuação. Um resultado não prediz o outro, então este
     * caminho existe para ser MEDIDO, não presumido.
     *
     * Duas decisões que não são estilo:
     *
     * <ul>
     *   <li>roda numa thread própria por obrigação. O motor posta o script na
     *       thread principal e bloqueia quem chamou até a resposta chegar; como
     *       {@code onReceive} já está na principal, chamar direto travaria as
     *       duas pontas e o job morreria de prazo sem nenhuma linha de trilha;</li>
     *   <li>a credencial sai do cofre, nunca de um extra do Intent. Extra de
     *       broadcast é texto legível por qualquer coisa que observe o despacho, e
     *       CPF não se transporta assim para economizar três linhas.</li>
     * </ul>
     */
    private void loginAgenciaWeb(final String request, final String property) {
        final Context context = getApplicationContext();
        final String imovel = property == null || property.trim().isEmpty() ? "casa" : property.trim();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    BillingConfig config = BillingConfig.load(context);
                    String document = config.value("equatorial_cpf");
                    String unit = config.value(imovel + "_energy");
                    RodLog.step("agenciaweb", "imovel=" + imovel
                        + " documento=" + RodLog.describe(document)
                        + " unidade=" + RodLog.describe(unit));
                    reply(request, true, EquatorialWebEngine.loginAgenciaWeb(
                        context, unit, document,
                        System.currentTimeMillis() + AGENCIAWEB_BUDGET_MILLIS), null);
                } catch (Exception error) {
                    reply(request, false, null,
                        error.getClass().getSimpleName() + ": " + error.getMessage());
                }
            }
        }, "rod-agenciaweb").start();
    }

    /**
     * Lê os débitos em aberto pelo canal {@code go.*}, sem chegar perto de pagar.
     *
     * Vale o esforço porque hoje o proprietário loga à mão no ASPX a cada menos
     * de vinte e quatro horas só para saber quanto deve. Se este canal entregar
     * valor e referência sozinho, o trabalho manual da CONSULTA acaba — e isso é
     * verdade mesmo sem PIX e sem código de barras, que continuam do outro lado
     * de um portão que não abrimos.
     *
     * A operação é separada da ponte de propósito: são perguntas diferentes, e
     * juntá-las faria um resultado bom esconder um resultado ruim.
     */
    private void readDebtsAgenciaWeb(final String request, final String property) {
        final Context context = getApplicationContext();
        final String imovel = property == null || property.trim().isEmpty() ? "casa" : property.trim();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    BillingConfig config = BillingConfig.load(context);
                    String document = config.value("equatorial_cpf");
                    String unit = config.value(imovel + "_energy");
                    RodLog.step("consulta", "imovel=" + imovel
                        + " documento=" + RodLog.describe(document)
                        + " unidade=" + RodLog.describe(unit));
                    reply(request, true, EquatorialWebEngine.readDebtsAgenciaWeb(
                        context, unit, document,
                        System.currentTimeMillis() + BRIDGE_BUDGET_MILLIS), null);
                } catch (Exception error) {
                    reply(request, false, null,
                        error.getClass().getSimpleName() + ": " + error.getMessage());
                }
            }
        }, "rod-consulta").start();
    }

    /**
     * Mede a ponte entre os dois portais da concessionária, com controle.
     *
     * Existe separada do login porque é um EXPERIMENTO, não um passo da consulta:
     * ela observa a área de faturas do {@code goias.*} antes, autentica no
     * {@code go.*}, segue apenas link visível do próprio portal e observa de
     * novo. O "antes" é o que impede a coincidência de passar por causa — uma
     * sessão que já estivesse viva daria ponte aberta sem o login ter feito nada.
     *
     * Mesmas duas regras do login: thread própria, porque o motor bloqueia quem
     * chama até o WebView responder e {@code onReceive} está na principal; e
     * credencial do cofre, nunca de extra de Intent, que é texto legível por
     * qualquer coisa que observe o despacho.
     */
    private void bridgeAgenciaWeb(final String request, final String property) {
        final Context context = getApplicationContext();
        final String imovel = property == null || property.trim().isEmpty() ? "casa" : property.trim();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    BillingConfig config = BillingConfig.load(context);
                    String document = config.value("equatorial_cpf");
                    String unit = config.value(imovel + "_energy");
                    RodLog.step("ponte", "imovel=" + imovel
                        + " documento=" + RodLog.describe(document)
                        + " unidade=" + RodLog.describe(unit));
                    reply(request, true, EquatorialWebEngine.bridgeAgenciaWeb(
                        context, unit, document,
                        System.currentTimeMillis() + BRIDGE_BUDGET_MILLIS), null);
                } catch (Exception error) {
                    reply(request, false, null,
                        error.getClass().getSimpleName() + ": " + error.getMessage());
                }
            }
        }, "rod-ponte").start();
    }

    private void probeEquatorialSession(String request, boolean afterSubmit, long deadline) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            if (System.currentTimeMillis() < deadline) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> probeEquatorialSession(request, afterSubmit, deadline), UI_POLL_MILLIS);
                return;
            }
            RodLog.fail("sessao", "Chrome nao entregou pagina nenhuma");
            replyState(request, EquatorialSession.State.BROWSER_STALE, false);
            return;
        }
        AccessibilityNodeInfo bill = findByViewId(root, BILL_SELECTOR_FIELD);
        boolean billSelector = bill != null;
        if (bill != null) bill.recycle();
        boolean loginFields = hasLoginFields(root);
        boolean reRendered = afterSubmit && loginFields && loginFormReRendered(root);

        List<String> textos = new ArrayList<>();
        collect(root, textos);
        root.recycle();
        // O texto nao entra na trilha: ele carrega valor, vencimento e linha digitavel.
        EquatorialSession.State state = EquatorialSession.classify(
            String.join("\n", textos), true, billSelector, loginFields, afterSubmit);

        // Depois de enviar credenciais, o formulario continuar na tela nao decide
        // nada: o portal gera um token de risco e so entao faz o postback, o que
        // leva alguns segundos. Aceitar isso como veredito reportava login falho
        // um instante antes de ele dar certo.
        boolean decidido = billSelector
            || (loginFields && !afterSubmit)
            || state == EquatorialSession.State.HUMAN_CHECK
            || state == EquatorialSession.State.LOGIN_REJECTED
            || (afterSubmit && state == EquatorialSession.State.LOGIN_OK);
        if (!decidido && System.currentTimeMillis() < deadline) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> probeEquatorialSession(request, afterSubmit, deadline), UI_POLL_MILLIS);
            return;
        }
        RodLog.step("sessao", "estado=" + state + " apos_envio=" + afterSubmit
            + " seletor=" + billSelector + " campos_login=" + loginFields
            + (afterSubmit && loginFields ? " formulario_recarregado=" + reRendered : ""));
        replyState(request, state, billSelector);
    }

    /**
     * O portal recarregou o formulario de acesso depois do envio?
     *
     * Assinatura observada no aparelho: a unidade volta preenchida pelo ViewState e
     * o documento volta em branco. Isso vai para a trilha como sinal e nada mais —
     * nao e declarado credencial recusada, porque o portal nao diz nada e a mesma
     * tela aparece quando a area autenticada esta fora do ar. Acusar o cadastro do
     * proprietario por um problema do portal custaria a ele tempo à procura de um
     * defeito que nao existe; quem encerra o job e o teto de tentativas.
     */
    private boolean loginFormReRendered(AccessibilityNodeInfo root) {
        AccessibilityNodeInfo unit = findByViewId(root, LOGIN_UNIT_FIELD);
        AccessibilityNodeInfo document = findByViewId(root, LOGIN_DOCUMENT_FIELD);
        boolean signature = unit != null && document != null
            && hasText(unit) && !hasText(document);
        if (unit != null) unit.recycle();
        if (document != null) document.recycle();
        return signature;
    }

    private static boolean hasText(AccessibilityNodeInfo node) {
        CharSequence text = node.getText();
        return text != null && text.toString().replaceAll("\\D", "").length() > 0;
    }

    private boolean hasLoginFields(AccessibilityNodeInfo root) {
        AccessibilityNodeInfo unit = findByViewId(root, LOGIN_UNIT_FIELD);
        AccessibilityNodeInfo document = findByViewId(root, LOGIN_DOCUMENT_FIELD);
        boolean both = unit != null && document != null;
        if (unit != null) unit.recycle();
        if (document != null) document.recycle();
        return both;
    }

    /**
     * Devolve o estado e se a segunda via ja esta na frente.
     *
     * O seletor viaja junto porque autenticar nao termina na segunda via: o portal
     * leva para a home da area logada. Sem esse dado o leitor tentaria escolher o
     * imovel numa pagina que nao tem o combo.
     */
    private void replyState(String request, EquatorialSession.State state, boolean billSelector) {
        try {
            reply(request, true, new JSONObject()
                .put("state", state.name())
                .put("selector", billSelector), null);
        } catch (Exception error) {
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: estado da sessao ilegivel");
        }
    }

    /**
     * Autentica na Agencia Virtual com as credenciais do cofre.
     *
     * Escrever num campo dispara JS que limpa o outro, entao preencher e enviar no
     * mesmo passo enviava formulario incompleto. Aqui preenche-se, reobserva-se a
     * arvore, confere-se, reaplica-se o que faltou, e so entao ENTRAR e acionado.
     * Nada do que e digitado aparece na trilha: so se o campo ficou preenchido.
     */
    private void loginEquatorial(String request, String unit, String document, int attempt) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            replyState(request, EquatorialSession.State.BROWSER_STALE, false);
            return;
        }
        AccessibilityNodeInfo unitField = findByViewId(root, LOGIN_UNIT_FIELD);
        AccessibilityNodeInfo documentField = findByViewId(root, LOGIN_DOCUMENT_FIELD);
        RodLog.found("login", "formulario de acesso na tela", unitField != null && documentField != null);
        if (unitField == null || documentField == null) {
            if (unitField != null) unitField.recycle();
            if (documentField != null) documentField.recycle();
            root.recycle();
            if (attempt == 0) {
                // A rota da segunda via sem sessao cai na pagina de suporte, nao no
                // login. Navegar explicitamente e o que traz o formulario.
                RodLog.step("login", "abrindo a tela de acesso do portal");
                openRoute(LOGIN_URL);
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> loginEquatorial(request, unit, document, 1), 3000);
                return;
            }
            if (attempt < 6) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> loginEquatorial(request, unit, document, attempt + 1), UI_POLL_MILLIS * 3);
                return;
            }
            // Sem formulario mesmo depois de navegar: quem responde e a observacao,
            // nao um palpite.
            probeEquatorialSession(request, true, System.currentTimeMillis() + CHOOSER_RENDER_MILLIS);
            return;
        }
        setNodeText(unitField, unit == null ? "" : unit);
        setNodeText(documentField, document == null ? "" : document);
        unitField.recycle();
        documentField.recycle();
        root.recycle();
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> submitEquatorialLogin(request, unit, document, 0), 900);
    }

    private void submitEquatorialLogin(String request, String unit, String document, int attempt) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            replyState(request, EquatorialSession.State.BROWSER_STALE, false);
            return;
        }
        AccessibilityNodeInfo unitField = findByViewId(root, LOGIN_UNIT_FIELD);
        AccessibilityNodeInfo documentField = findByViewId(root, LOGIN_DOCUMENT_FIELD);
        if (unitField == null || documentField == null) {
            if (unitField != null) unitField.recycle();
            if (documentField != null) documentField.recycle();
            root.recycle();
            probeEquatorialSession(request, true, System.currentTimeMillis() + LOGIN_SETTLE_MILLIS);
            return;
        }
        boolean unitOk = filled(unitField, unit);
        boolean documentOk = filled(documentField, document);
        RodLog.step("login", "tentativa " + attempt + " unidade_ok=" + unitOk
            + " documento_ok=" + documentOk);
        if (!unitOk) setNodeText(unitField, unit == null ? "" : unit);
        if (!documentOk) setNodeText(documentField, document == null ? "" : document);
        unitField.recycle();
        documentField.recycle();
        if (!unitOk || !documentOk) {
            root.recycle();
            if (attempt >= LOGIN_FILL_ATTEMPTS) {
                RodLog.fail("login", "formulario nao ficou completo; nao vou enviar pela metade");
                replyState(request, EquatorialSession.State.LOGIN_IN_PROGRESS, false);
                return;
            }
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> submitEquatorialLogin(request, unit, document, attempt + 1), 900);
            return;
        }
        boolean clicked = gestureClickExact(root, "entrar");
        if (!clicked) clicked = gestureClickLabel(root, "entrar");
        if (!clicked) clicked = clickFirst(root, "entrar");
        root.recycle();
        RodLog.step("login", "ENTRAR acionado=" + clicked);
        if (!clicked) {
            replyState(request, EquatorialSession.State.LOGIN_IN_PROGRESS, false);
            return;
        }
        // dispatchGesture devolve true sem a pagina reagir; o veredito e a proxima
        // observacao da arvore, com prazo proprio.
        probeEquatorialSession(request, true, System.currentTimeMillis() + LOGIN_SETTLE_MILLIS);
    }

    /** O campo contem exatamente os digitos esperados? Mascara do portal nao conta. */
    private static boolean filled(AccessibilityNodeInfo field, String expected) {
        if (expected == null || expected.isEmpty()) return true;
        CharSequence text = field.getText();
        if (text == null) return false;
        return text.toString().replaceAll("\\D", "").equals(expected.replaceAll("\\D", ""));
    }

    /**
     * Toque num no cujo rotulo e exatamente o esperado.
     *
     * Casar por substring achava "Entrar em contato" antes do botao ENTRAR e o
     * login nunca era enviado. Igualdade, sem acento e sem caixa, resolve; a busca
     * por substring continua como segunda tentativa.
     */
    private boolean gestureClickExact(AccessibilityNodeInfo node, String label) {
        String value = EquatorialSession.fold(
            (node.getText() == null ? "" : node.getText().toString()) + " "
                + (node.getContentDescription() == null ? "" : node.getContentDescription().toString()))
            .trim();
        if (value.equals(label) || value.equals(label + " ") || value.replace(" ", "").equals(label)) {
            Rect bounds = new Rect();
            node.getBoundsInScreen(bounds);
            if (!bounds.isEmpty()) {
                Path path = new Path();
                path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                return dispatchGesture(new GestureDescription.Builder()
                    .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = gestureClickExact(child, label);
                child.recycle();
                if (clicked) return true;
            }
        }
        return false;
    }

    /**
     * Recuperacao do navegador, em degraus, sem nunca tocar em dados de terceiros.
     *
     * Degrau 1 recarrega a rota autenticada. Degrau 2 encerra a sessao do portal
     * pelo proprio "Sair" e reabre a rota, o que descarta a pagina presa sem
     * apagar cookie, cache ou aba de nenhum outro site. `pm clear` do Chrome e
     * limpeza de dados de navegacao estao fora de questao: derrubariam a sessao de
     * tudo que o proprietario usa no aparelho para consertar uma pagina nossa.
     */
    private void recoverChrome(String request, String mode, int attempt) {
        boolean reopen = "reopen".equals(mode);
        if (attempt == 0) {
            RodLog.step("recuperacao", "degrau=" + (reopen ? "reabrir" : "recarregar"));
            openRoute(reopen ? LOGOUT_URL : SEGUNDA_VIA_URL);
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> recoverChrome(request, mode, 1), 2500);
            return;
        }
        if (reopen && attempt == 1) {
            openRoute(SEGUNDA_VIA_URL);
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> recoverChrome(request, mode, 2), 3000);
            return;
        }
        probeEquatorialSession(request, false, System.currentTimeMillis() + CHOOSER_RENDER_MILLIS);
    }

    /**
     * Abre uma rota do portal na aba do ROD.
     *
     * EXTRA_APPLICATION_ID faz o Chrome REUSAR a aba que este app abriu em vez de
     * criar outra. Sem ele o aparelho acumulou 72 abas do portal, e cada abertura
     * deixava o navegador mais pesado e mais propenso a nao renderizar a pagina.
     */
    private void openRoute(String url) {
        Intent browser = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
        browser.setPackage(CHROME)
            .putExtra(Browser.EXTRA_APPLICATION_ID, getPackageName())
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        startActivity(browser);
    }


    /**
     * Agencia Virtual da Equatorial: unidade consumidora e CPF.
     *
     * A home so pede o CPF e leva para esta tela. Aqui os campos tem
     * resource-id estavel, entao nao dependemos da ordem em que aparecem.
     */
    /**
     * Abre o portal e confirma que o Chrome realmente ficou visivel.
     *
     * O passo antigo disparava o Intent e respondia "ok" em poucos milissegundos,
     * sem verificar nada. Com a tela apagada o Chrome nao chegava a aparecer para
     * o servico de acessibilidade e o passo seguinte falhava sem explicacao.
     *
     * O SCREEN_BRIGHT_WAKE_LOCK usado pelo leitor esta depreciado desde a API 17 e
     * nao acende mais a tela no Android 12; por isso a WakeActivity, que ja servia
     * ao fluxo da Saneago, passa a valer tambem aqui.
     */
    private void openEquatorial(String request, int attempt) {
        if (attempt == 0) {
            RodLog.step("abertura", "acordando a tela antes de abrir o portal");
            performGlobalAction(GLOBAL_ACTION_HOME);
            startActivity(new Intent(this, WakeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            new Handler(Looper.getMainLooper()).postDelayed(() -> openEquatorial(request, 1), 1600);
            return;
        }
        if (attempt == 1) {
            RodLog.step("abertura", "abrindo o portal no Chrome");
            openRoute(SEGUNDA_VIA_URL);
            new Handler(Looper.getMainLooper()).postDelayed(() -> openEquatorial(request, 2), 3000);
            return;
        }
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root != null) {
            root.recycle();
            RodLog.step("abertura", "Chrome visivel na tentativa " + attempt);
            reply(request, true, new JSONObject(), null);
            return;
        }
        if (attempt >= 6) {
            RodLog.fail("abertura", "Chrome nao ficou visivel");
            reply(request, false, null, "O Chrome nao abriu o portal da Equatorial");
            return;
        }
        RodLog.step("abertura", "Chrome ainda nao visivel, aguardando (tentativa " + attempt + ")");
        new Handler(Looper.getMainLooper()).postDelayed(() -> openEquatorial(request, attempt + 1), 1500);
    }

    private static AccessibilityNodeInfo findByViewId(AccessibilityNodeInfo node, String suffix) {
        String viewId = node.getViewIdResourceName();
        if (viewId != null && viewId.endsWith(suffix)) return AccessibilityNodeInfo.obtain(node);
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                AccessibilityNodeInfo found = findByViewId(child, suffix);
                child.recycle();
                if (found != null) return found;
            }
        }
        return null;
    }

    /**
     * Abre o seletor de imovel da area autenticada.
     *
     * A home autenticada mostra "Selecione Unidade Consumidora" com o contrato
     * corrente. O portal identifica cada imovel por conta contrato, que e um
     * numero diferente da unidade consumidora guardada no cofre, entao a
     * correspondencia e tentada pelos dois: primeiro pelos digitos configurados,
     * e so entao abrindo a lista para inspecao.
     */
    /**
     * Escolhe a unidade consumidora no combo da propria pagina de segunda via.
     *
     * O portal grava a UC com zeros a esquerda ate quinze digitos, enquanto o
     * cofre guarda o numero como o proprietario o conhece. Normalizar os dois
     * lados resolve a correspondencia sem cadastro extra: nao ha dois numeros,
     * ha o mesmo numero escrito de dois jeitos.
     */
    private void selectEquatorialContract(String request, String expectedUnit, int attempt) {
        String expected = expectedUnit == null ? "" : digits(expectedUnit);
        if (expected.isEmpty()) {
            reply(request, false, null,
                "EQUATORIAL_PROPERTY_NOT_MAPPED: nenhuma unidade consumidora configurada para este imovel");
            return;
        }
        awaitBillPage(request, expected, System.currentTimeMillis() + CHOOSER_RENDER_MILLIS);
    }

    /**
     * Aguarda a pagina de segunda via renderizar antes de julgar o estado.
     *
     * Chrome ficar visivel nao quer dizer que a pagina carregou: a arvore ainda
     * mostra a navegacao anterior por algumas centenas de milissegundos. Decidir
     * ali reportava sessao expirada tendo sessao valida, que e o pior tipo de
     * erro: manda o dono resolver um problema que nao existe. So depois do prazo,
     * com a tela de login ainda na frente, a sessao e dada como caida.
     */
    private void awaitBillPage(String request, String expected, long deadline) {
        AccessibilityNodeInfo pagina = packageRoot(CHROME);
        if (pagina == null) {
            if (System.currentTimeMillis() < deadline) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> awaitBillPage(request, expected, deadline), UI_POLL_MILLIS);
                return;
            }
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: portal nao ficou visivel");
            return;
        }
        AccessibilityNodeInfo combo = findByViewId(pagina, "CONTENT_comboBoxUC");
        boolean pronta = combo != null;
        if (combo != null) combo.recycle();

        List<String> textos = new ArrayList<>();
        collect(pagina, textos);
        pagina.recycle();
        EquatorialTextParser.Page estado =
            EquatorialTextParser.parse(String.join(System.lineSeparator(), textos));

        if (pronta) {
            RodLog.step("contrato", "pagina de segunda via pronta");
            pickSelectDigits(request, "CONTENT_comboBoxUC", expected, 0,
                () -> reply(request, true, new JSONObject(), null));
            return;
        }
        if (estado.state == EquatorialTextParser.State.HUMAN_CHECK) {
            reply(request, false, null,
                "EQUATORIAL_HUMAN_CHECK: a Equatorial pediu verificacao humana");
            return;
        }
        if (System.currentTimeMillis() < deadline) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> awaitBillPage(request, expected, deadline), UI_POLL_MILLIS);
            return;
        }
        if (estado.state == EquatorialTextParser.State.AUTH_REQUIRED) {
            RodLog.step("contrato", "Agencia Virtual continuou pedindo autenticacao");
            reply(request, false, null,
                "EQUATORIAL_AUTH_REQUIRED: a sessao da Agencia Virtual expirou no Poco");
            return;
        }
        RodLog.fail("contrato", "combo de unidade ausente apos o prazo");
        reply(request, false, null,
            "EQUATORIAL_BILL_NOT_FOUND: a pagina de segunda via nao carregou o seletor de unidade");
    }

    /** Escolhe num <select> a opcao cujos digitos, normalizados, casam com o esperado. */
    private void pickSelectDigits(String request, String viewId, String expected, int attempt, Runnable next) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: portal saiu da frente ao escolher o imovel");
            return;
        }
        AccessibilityNodeInfo field = findByViewId(root, viewId);
        if (field == null) {
            root.recycle();
            if (attempt < 4) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> pickSelectDigits(request, viewId, expected, attempt + 1, next), UI_POLL_MILLIS * 3);
                return;
            }
            RodLog.fail("contrato", "combo de unidade ausente na pagina");
            reply(request, false, null, "EQUATORIAL_BILL_NOT_FOUND: pagina de segunda via nao carregou o seletor de unidade");
            return;
        }
        boolean tapped = gestureClickNodeCenter(field);
        field.recycle();
        root.recycle();
        RodLog.step("contrato", "combo de unidade toque=" + tapped + " tentativa=" + attempt);
        awaitDigitsDialog(request, viewId, expected, attempt, next,
            System.currentTimeMillis() + DIALOG_OPEN_MILLIS);
    }

    private void awaitDigitsDialog(String request, String viewId, String expected, int attempt,
                                   Runnable next, long deadline) {
        AccessibilityNodeInfo dialog = contractDialogRoot();
        if (dialog == null) {
            if (System.currentTimeMillis() < deadline) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> awaitDigitsDialog(request, viewId, expected, attempt, next, deadline), UI_POLL_MILLIS);
                return;
            }
            if (attempt < 2) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> pickSelectDigits(request, viewId, expected, attempt + 1, next), UI_POLL_MILLIS);
                return;
            }
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: a lista de unidades nao abriu");
            return;
        }
        Rect window = new Rect();
        dialog.getBoundsInScreen(window);
        int opcoes = countContractOptions(dialog);
        boolean picked = gestureClickContractDigits(dialog, expected, window);
        dialog.recycle();
        RodLog.step("contrato", "unidades na lista=" + opcoes + " imovel escolhido=" + picked);
        if (!picked) {
            // Sem correspondencia, ler seria adivinhar de quem e a fatura.
            closeContractDialog(request, 0, opcoes > 0
                ? "EQUATORIAL_PROPERTY_NOT_MAPPED: a unidade configurada nao esta entre as " + opcoes
                    + " deste login"
                : "EQUATORIAL_CONTRACT_NOT_FOUND: a lista de unidades veio vazia");
            return;
        }
        awaitSelectDialogClosed(request, next, System.currentTimeMillis() + DIALOG_CLOSE_MILLIS);
    }

    /**
     * Encerra o passo com a lista fora da frente. So termina em falha: fechar com
     * BACK cancela a escolha, entao nao ha sucesso possivel por este caminho.
     */
    /** Quantas opcoes a lista oferece. So a contagem; nunca os valores. */
    private static int countContractOptions(AccessibilityNodeInfo node) {
        int total = 0;
        String viewId = node.getViewIdResourceName();
        CharSequence text = node.getText();
        if (viewId != null && viewId.endsWith("id/text1") && text != null && text.length() > 0) total++;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) { total += countContractOptions(child); child.recycle(); }
        }
        return total;
    }

    private void closeContractDialog(String request, int attempt, String error) {
        AccessibilityNodeInfo dialog = contractDialogRoot();
        if (dialog == null) {
            RodLog.step("contrato", "lista fora da frente na tentativa " + attempt);
            reply(request, false, null, error);
            return;
        }
        dialog.recycle();
        if (attempt >= CONTRACT_CLOSE_ATTEMPTS) {
            RodLog.fail("contrato", "lista nativa continuou aberta apos " + attempt + " tentativas de fechar");
            reply(request, false, null, error);
            return;
        }
        performGlobalAction(GLOBAL_ACTION_BACK);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> closeContractDialog(request, attempt + 1, error), UI_POLL_MILLIS * 2);
    }

    /**
     * Raiz do dialogo nativo do seletor, ou null quando ele nao esta na frente.
     *
     * Basta "janela ativa que nao e do Chrome" para confundir launcher e
     * notificacao com o seletor, entao os ids do dialogo sao conferidos.
     */
    private AccessibilityNodeInfo contractDialogRoot() {
        AccessibilityNodeInfo active = getRootInActiveWindow();
        if (active == null) {
            RodLog.step("diag", "janela ativa: nenhuma");
            return null;
        }
        // Diagnóstico de forma, nunca de conteúdo: só o nome do pacote dono da
        // janela e quais marcadores de diálogo existem. Descobrir isso por
        // uiautomator não é possível: ele disputa o mesmo canal de
        // acessibilidade e derruba este serviço no meio do fluxo.
        CharSequence owner = active.getPackageName();
        StringBuilder markers = new StringBuilder();
        for (String id : DIALOG_VIEW_IDS) {
            AccessibilityNodeInfo probe = findByViewId(active, id);
            if (probe != null) { probe.recycle(); markers.append(id).append(' '); }
        }
        RodLog.step("diag", "janela ativa pertence a " + (owner == null ? "?" : owner)
            + " | marcadores: " + (markers.length() == 0 ? "nenhum" : markers.toString().trim()));

        // O Chrome desenha o <select> como diálogo do próprio processo, então a
        // janela ainda pertence a ele. Descartar por pacote perdia a lista; o que
        // distingue de fato é a presença dos marcadores do AlertDialog.
        if (markers.length() > 0) return active;
        active.recycle();
        return null;
    }

    /** Ha uma janela do sistema na frente do Chrome? Só nela o BACK e seguro. */
    private boolean systemDialogInFront() {
        AccessibilityNodeInfo active = getRootInActiveWindow();
        if (active == null) return false;
        boolean chrome = belongsTo(active, CHROME);
        active.recycle();
        return !chrome;
    }

    /** Toque no centro de um no ja localizado. */
    private boolean gestureClickNode(AccessibilityNodeInfo node) {
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        if (bounds.isEmpty()) return false;
        Path path = new Path();
        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
        return dispatchGesture(new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
    }

    /**
     * Preenche o formulario oficial de segunda via e emite.
     *
     * O portal exige tipo de emissao e motivo antes de mostrar a fatura. Sao
     * campos de consulta, nao ordem de servico: nada e pago, negociado ou
     * alterado. O motivo escolhido e "Outros", a unica opcao que nao afirma algo
     * falso em nome do proprietario.
     *
     * Cada <select> vira dialogo nativo do Chrome, entao a escolha reusa a mesma
     * maquina de estados do seletor de imovel: abre, confirma aberto, escolhe,
     * confirma fechado.
     */
    private void emitEquatorial(String request) {
        // "Fatura completa" navega para SegundaViaDownload.aspx e produz um PDF, que
        // nao serve para leitura pela arvore. "Apenas codigo de barras" e a opcao
        // que renderiza o dado de pagamento na propria tela, que e o que o ROD
        // precisa entregar.
        pickSelect(request, "CONTENT_cbTipoEmissao", new String[]{"codigo de barras", "código de barras"}, 0,
            () -> pickSelect(request, "CONTENT_cbMotivo", new String[]{"outros"}, 0,
                () -> pressEmitir(request, 0)));
    }

    /** Escolhe uma opcao de um <select> da pagina pelo rotulo, via dialogo nativo. */
    private void pickSelect(String request, String viewId, String[] labels, int attempt, Runnable next) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: portal saiu da frente ao preencher o formulario");
            return;
        }
        AccessibilityNodeInfo field = findByViewId(root, viewId);
        if (field == null) {
            root.recycle();
            if (attempt < 3) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> pickSelect(request, viewId, labels, attempt + 1, next), UI_POLL_MILLIS * 3);
                return;
            }
            RodLog.fail("emissao", "campo ausente: " + viewId);
            reply(request, false, null, "EQUATORIAL_BILL_NOT_FOUND: formulario de segunda via nao apareceu");
            return;
        }
        boolean tapped = gestureClickNodeCenter(field);
        field.recycle();
        root.recycle();
        RodLog.step("emissao", viewId + " toque=" + tapped + " tentativa=" + attempt);
        awaitSelectDialog(request, viewId, labels, attempt, next,
            System.currentTimeMillis() + DIALOG_OPEN_MILLIS);
    }

    private void awaitSelectDialog(String request, String viewId, String[] labels, int attempt,
                                   Runnable next, long deadline) {
        AccessibilityNodeInfo dialog = contractDialogRoot();
        if (dialog == null) {
            if (System.currentTimeMillis() < deadline) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> awaitSelectDialog(request, viewId, labels, attempt, next, deadline), UI_POLL_MILLIS);
                return;
            }
            if (attempt < 2) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> pickSelect(request, viewId, labels, attempt + 1, next), UI_POLL_MILLIS);
                return;
            }
            RodLog.fail("emissao", "lista de " + viewId + " nao abriu");
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: a lista do formulario nao abriu");
            return;
        }
        Rect window = new Rect();
        dialog.getBoundsInScreen(window);
        boolean picked = gestureClickDialogLabel(dialog, labels, window);
        dialog.recycle();
        RodLog.step("emissao", viewId + " opcao escolhida=" + picked);
        if (!picked) {
            closeContractDialog(request, 0,
                "EQUATORIAL_BILL_NOT_FOUND: opcao esperada ausente na lista do formulario");
            return;
        }
        awaitSelectDialogClosed(request, next, System.currentTimeMillis() + DIALOG_CLOSE_MILLIS);
    }

    private void awaitSelectDialogClosed(String request, Runnable next, long deadline) {
        AccessibilityNodeInfo dialog = contractDialogRoot();
        boolean open = dialog != null;
        if (dialog != null) dialog.recycle();
        if (!open) {
            AccessibilityNodeInfo root = packageRoot(CHROME);
            boolean back = root != null;
            if (root != null) root.recycle();
            if (back) { next.run(); return; }
        }
        if (System.currentTimeMillis() < deadline) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> awaitSelectDialogClosed(request, next, deadline), UI_POLL_MILLIS);
            return;
        }
        closeContractDialog(request, 0,
            "EQUATORIAL_PORTAL_TIMEOUT: a lista do formulario nao fechou");
    }

    /** Pressiona Emitir e confirma que a pagina reagiu. */
    private void pressEmitir(String request, int attempt) {
        AccessibilityNodeInfo root = packageRoot(CHROME);
        if (root == null) {
            reply(request, false, null, "EQUATORIAL_PORTAL_TIMEOUT: portal saiu da frente antes de emitir");
            return;
        }
        AccessibilityNodeInfo botao = findByViewId(root, "CONTENT_btEnviar");
        boolean pressed = botao != null && gestureClickNodeCenter(botao);
        if (botao != null) botao.recycle();
        if (!pressed) pressed = gestureClickLabel(root, "emitir");
        root.recycle();
        RodLog.step("emissao", "Emitir pressionado=" + pressed + " tentativa=" + attempt);
        if (!pressed) {
            if (attempt < 2) {
                new Handler(Looper.getMainLooper()).postDelayed(
                    () -> pressEmitir(request, attempt + 1), UI_POLL_MILLIS * 3);
                return;
            }
            reply(request, false, null, "EQUATORIAL_BILL_NOT_FOUND: botao Emitir nao encontrado");
            return;
        }
        // O postback do ASP.NET recarrega a pagina; a leitura seguinte espera por
        // estado, entao aqui basta devolver o controle.
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> reply(request, true, new JSONObject(), null), 2500);
    }

    /**
     * Toque no centro de um no ja localizado.
     *
     * Um elemento abaixo da dobra existe na arvore mas chega sem retangulo, e o
     * gesto nao tem onde cair. Pedir ao Chrome que o traga para a viewport e
     * devolver falso faz a tentativa seguinte encontra-lo ja visivel, em vez de
     * repetir o mesmo toque no vazio.
     */
    private boolean gestureClickNodeCenter(AccessibilityNodeInfo node) {
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        if (bounds.isEmpty() || !node.isVisibleToUser()) {
            node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN.getId());
            return false;
        }
        Path path = new Path();
        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
        return dispatchGesture(new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
    }

    /** Escolhe, dentro do dialogo nativo, a opcao cujo rotulo casa. */
    private boolean gestureClickDialogLabel(AccessibilityNodeInfo node, String[] labels, Rect window) {
        CharSequence text = node.getText();
        if (text != null && text.length() > 0 && node.isVisibleToUser()) {
            String value = text.toString().toLowerCase();
            for (String label : labels) {
                if (value.contains(label)) {
                    Rect bounds = new Rect();
                    node.getBoundsInScreen(bounds);
                    if (!bounds.isEmpty() && window.contains(bounds.centerX(), bounds.centerY())) {
                        Path path = new Path();
                        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                        return dispatchGesture(new GestureDescription.Builder()
                            .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
                    }
                }
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = gestureClickDialogLabel(child, labels, window);
                child.recycle();
                if (clicked) return true;
            }
        }
        return false;
    }

    /**
     * Espera a pagina assentar num estado reconhecivel e le somente o necessario.
     *
     * dispatchGesture e ACTION_CLICK devolvem true sem que a pagina tenha reagido,
     * entao nao da para confiar no retorno de uma acao: o que vale e reobservar a
     * arvore. Sessao expirada e desafio antibot sao terminais na hora, porque
     * insistir cegamente neles so gasta bateria e atrasa o aviso ao proprietario.
     */
    private void readEquatorial(String request, String expectedUnit) {
        settleEquatorial(request, expectedUnit, System.currentTimeMillis() + PAGE_SETTLE_MILLIS);
    }

    private void settleEquatorial(String request, String expectedUnit, long deadline) {
        try {
            AccessibilityNodeInfo root = packageRoot(CHROME);
            if (root == null) {
                if (System.currentTimeMillis() < deadline) {
                    retryEquatorial(request, expectedUnit, deadline);
                    return;
                }
                reply(request, false, null,
                    "EQUATORIAL_PORTAL_TIMEOUT: o portal nao ficou visivel no Chrome para a leitura");
                return;
            }
            List<String> values = new ArrayList<>();
            collect(root, values);
            root.recycle();
            // O conteudo nunca entra na trilha: a tela carrega valor, vencimento,
            // linha digitavel e PIX.
            EquatorialTextParser.Page page = EquatorialTextParser.parse(String.join("\n", values));

            if (page.state == EquatorialTextParser.State.AUTH_REQUIRED) {
                RodLog.step("equatorial", "portal devolveu a tela de autenticacao");
                reply(request, false, null,
                    "EQUATORIAL_AUTH_REQUIRED: a sessao da Equatorial expirou no Poco");
                return;
            }
            if (page.state == EquatorialTextParser.State.HUMAN_CHECK) {
                RodLog.step("equatorial", "portal exibiu desafio de verificacao humana");
                reply(request, false, null,
                    "EQUATORIAL_HUMAN_CHECK: a Equatorial pediu verificacao humana");
                return;
            }
            if (page.state == EquatorialTextParser.State.NO_BILL) {
                if (System.currentTimeMillis() < deadline) {
                    retryEquatorial(request, expectedUnit, deadline);
                    return;
                }
                RodLog.step("equatorial", "sessao valida, porem sem fatura nesta tela");
                reply(request, false, null,
                    "EQUATORIAL_BILL_NOT_FOUND: nenhuma fatura visivel na tela do imovel");
                return;
            }

            // A confirmacao do imovel acontece a montante: o passo de selecao casa
            // o valor exato da unidade no combo, confirma o dialogo fechar e o
            // portal voltar, e so entao emite. A tela de resultado desta rota nao
            // repete a unidade, e exigir que ela repita rejeitava faturas legitimas.
            // Se a tela exibir uma unidade, ela ainda tem de bater: divergencia
            // continua sendo recusa, porque atribuir a conta de um imovel a outro
            // e o pior erro possivel aqui.
            String shown = page.get("uc").replaceAll("\\D", "");
            String expected = expectedUnit == null ? "" : expectedUnit.replaceAll("\\D", "");
            RodLog.found("equatorial", "unidade repetida na tela", !shown.isEmpty());
            if (!shown.isEmpty() && !expected.isEmpty() && !shown.equals(expected)) {
                RodLog.fail("equatorial", "a tela mostra outro imovel");
                reply(request, false, null,
                    "EQUATORIAL_CONTRACT_NOT_FOUND: a tela mostra outra unidade consumidora");
                return;
            }

            RodLog.found("equatorial", "valor", !page.get("amount").isEmpty());
            RodLog.found("equatorial", "vencimento", !page.get("due_date").isEmpty());
            RodLog.found("equatorial", "referencia", !page.get("reference").isEmpty());
            RodLog.found("equatorial", "codigo de barras", !page.get("barcode").isEmpty());
            RodLog.found("equatorial", "pix", !page.get("pix").isEmpty());

            JSONObject result = new JSONObject()
                .put("source", "equatorial_chrome_session")
                .put("amount", page.get("amount"))
                .put("due_date", page.get("due_date"))
                .put("reference", page.get("reference"))
                .put("barcode", page.get("barcode"))
                .put("pix", page.get("pix"));
            reply(request, true, result, null);
        } catch (Exception error) {
            reply(request, false, null, error.getClass().getSimpleName() + ": " + error.getMessage());
        }
    }

    private void retryEquatorial(String request, String expectedUnit, long deadline) {
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> settleEquatorial(request, expectedUnit, deadline), PAGE_POLL_MILLIS);
    }

    private static void collectEditors(AccessibilityNodeInfo node, List<AccessibilityNodeInfo> result) {
        if (node.isEditable() || "android.widget.EditText".contentEquals(node.getClassName()))
            result.add(AccessibilityNodeInfo.obtain(node));
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) { collectEditors(child, result); child.recycle(); }
        }
    }

    /** Editores da pagina, excluindo a barra de endereco editavel do Chrome. */
    private static void collectWebEditors(AccessibilityNodeInfo node,
                                          List<AccessibilityNodeInfo> result) {
        String id = node.getViewIdResourceName();
        boolean browserToolbar = id != null && id.startsWith("com.android.chrome:id/");
        if (!browserToolbar && (node.isEditable()
                || "android.widget.EditText".contentEquals(node.getClassName())))
            result.add(AccessibilityNodeInfo.obtain(node));
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                collectWebEditors(child, result);
                child.recycle();
            }
        }
    }

    private static void setNodeText(AccessibilityNodeInfo node, String value) {
        android.os.Bundle arguments = new android.os.Bundle();
        arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
    }

    private static boolean clickFirst(AccessibilityNodeInfo node, String... labels) {
        String value = ((node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription())).toLowerCase();
        for (String label : labels) if (value.contains(label) && node.isClickable())
            return node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = clickFirst(child, labels);
                child.recycle();
                if (clicked) return true;
            }
        }
        return false;
    }

    private boolean gestureClickLabel(AccessibilityNodeInfo node, String... labels) {
        String value = ClaraConversation.fold(
            (node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription()));
        for (String label : labels) {
            if (value.contains(ClaraConversation.fold(label))) {
                Rect bounds = new Rect();
                node.getBoundsInScreen(bounds);
                if (!bounds.isEmpty()) {
                    Path path = new Path();
                    path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                    GestureDescription gesture = new GestureDescription.Builder()
                        .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build();
                    return dispatchGesture(gesture, null, null);
                }
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = gestureClickLabel(child, labels);
                child.recycle();
                if (clicked) return true;
            }
        }
        return false;
    }

    private boolean gestureClickDigits(AccessibilityNodeInfo node, String expected) {
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = gestureClickDigits(child, expected);
                child.recycle();
                if (clicked) return true;
            }
        }
        String value = digits((node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription()));
        if (value.contains(expected)) {
            Rect bounds = new Rect();
            node.getBoundsInScreen(bounds);
            if (!bounds.isEmpty()) {
                Path path = new Path();
                path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                return dispatchGesture(new GestureDescription.Builder()
                    .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
            }
        }
        return false;
    }

    /**
     * Toca no item cujo numero e exatamente o esperado e que esta mesmo visivel.
     *
     * Duas armadilhas resolvidas aqui. Casar por substring clicava um numero mais
     * longo que apenas contivesse o esperado. E tocar no centro de um item rolado
     * para fora da janela acerta outro lugar da tela, com dispatchGesture devolvendo
     * true nos dois casos.
     *
     * gestureClickDigits fica como esta: o fluxo da Saneago depende do comportamento
     * atual dele e funciona hoje.
     */
    private boolean gestureClickContractDigits(AccessibilityNodeInfo node, String expected, Rect window) {
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean clicked = gestureClickContractDigits(child, expected, window);
                child.recycle();
                if (clicked) return true;
            }
        }
        String value = (node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription());
        if (!ContractMatch.matches(value, expected)) return false;
        if (!node.isVisibleToUser()) return false;
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        if (bounds.isEmpty()) return false;
        if (!window.isEmpty() && !ContractMatch.centerInside(
                bounds.left, bounds.top, bounds.right, bounds.bottom,
                window.left, window.top, window.right, window.bottom)) return false;
        Path path = new Path();
        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
        return dispatchGesture(new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build(), null, null);
    }

    /**
     * Regras puras de identificacao do contrato. Sem Android, para serem testaveis.
     *
     * Classe aninhada estatica de proposito: carrega-la num teste de JVM nao
     * inicializa o servico de acessibilidade em volta.
     */
    static final class ContractMatch {
        /**
         * Tudo que nao seja digito ou separador interno de um numero quebra o token.
         *
         * Ponto, hifen, espaco e espaco fixo aparecem dentro de um mesmo numero na
         * tela ("1234 5678", "1.234.567-8"); letras e virgula separam numeros
         * diferentes.
         */
        private static final String TOKEN_SEPARATOR = "[^0-9.\\- \\u00a0]+";

        private ContractMatch() { }

        /** So os digitos, sem zeros a esquerda: a tela e o cofre escrevem diferente. */
        static String normalize(String value) {
            if (value == null) return "";
            String result = value.replaceAll("\\D", "");
            return result.replaceFirst("^0+(?!$)", "");
        }

        /**
         * O texto contem o numero esperado inteiro, e nao como pedaco de outro.
         *
         * Comparar token a token e o que impede 123456789 de ser aceito como
         * 12345678 e uma fatura ser atribuida ao imovel errado.
         */
        static boolean matches(String raw, String expected) {
            String target = normalize(expected);
            if (target.isEmpty() || raw == null) return false;
            for (String token : raw.split(TOKEN_SEPARATOR))
                if (normalize(token).equals(target)) return true;
            return false;
        }

        /** O centro do item cai dentro da janela? Fora dela o toque acerta outra coisa. */
        static boolean centerInside(int left, int top, int right, int bottom,
                                    int windowLeft, int windowTop, int windowRight, int windowBottom) {
            if (right <= left || bottom <= top) return false;
            int x = (left + right) / 2;
            int y = (top + bottom) / 2;
            return x >= windowLeft && x <= windowRight && y >= windowTop && y <= windowBottom;
        }
    }

    private static boolean containsDigits(AccessibilityNodeInfo node, String expected) {
        String value = digits((node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription()));
        if (value.contains(expected)) return true;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean found = containsDigits(child, expected);
                child.recycle();
                if (found) return true;
            }
        }
        return false;
    }

    private static boolean containsLabel(AccessibilityNodeInfo node, String label) {
        String value = ClaraConversation.fold(
            (node.getText() == null ? "" : node.getText()) + " " +
            (node.getContentDescription() == null ? "" : node.getContentDescription()));
        if (value.contains(ClaraConversation.fold(label))) return true;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean found = containsLabel(child, label);
                child.recycle();
                if (found) return true;
            }
        }
        return false;
    }

    private static String digits(String value) {
        return ContractMatch.normalize(value);
    }

    private JSONObject readSaneago() throws Exception {
        AccessibilityNodeInfo root = saneagoRoot();
        if (root == null) throw new IllegalStateException("Janela Saneago nao encontrada");
        List<String> text = new ArrayList<>();
        collect(root, text);
        root.recycle();
        for (String value : text) {
            String normalized = value.toLowerCase();
            if (normalized.contains("faça login") || normalized.contains("faca login") ||
                    normalized.contains("abrir tela de login"))
                throw new IllegalStateException("Sessao Saneago expirada");
        }
        JSONObject result = new JSONObject()
            .put("source", "saneago_android_app")
            .put("account", valueAfterPrefix(text, "Conta:"))
            .put("amount", valueAfter(text, "Fatura atual"))
            .put("reference", valueAfter(text, "Referencia", "Referência"))
            .put("due_date", valueAfter(text, "Vencimento"))
            .put("consumption", valueAfter(text, "Consumo"))
            .put("read_only", true);
        if (result.getString("account").isEmpty() || result.getString("amount").isEmpty() ||
                result.getString("reference").isEmpty() || result.getString("due_date").isEmpty() ||
                result.getString("consumption").isEmpty())
            throw new IllegalStateException("Campos financeiros ainda indisponiveis");
        return result;
    }

    private void readSaneagoWithFallback(String request) {
        try {
            reply(request, true, readSaneago(), null);
        } catch (Exception error) {
            if (error.getMessage() != null && error.getMessage().contains("Sessao Saneago expirada")) {
                reply(request, false, null, error.getMessage());
                return;
            }
            readSaneagoOcr(request);
        }
    }

    private void readSaneagoOcr(String request) {
        readSaneagoOcr(request, true);
    }

    private void readSaneagoOcr(String request, boolean allowLoginRecovery) {
        takeScreenshot(Display.DEFAULT_DISPLAY, getMainExecutor(), new TakeScreenshotCallback() {
            @Override public void onSuccess(ScreenshotResult screenshot) {
                HardwareBuffer buffer = screenshot.getHardwareBuffer();
                ColorSpace colorSpace = screenshot.getColorSpace();
                Bitmap hardware = Bitmap.wrapHardwareBuffer(buffer, colorSpace);
                if (hardware == null) {
                    buffer.close();
                    reply(request, false, null, "Screenshot sem bitmap");
                    return;
                }
                Bitmap bitmap = hardware.copy(Bitmap.Config.ARGB_8888, false);
                buffer.close();
                TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
                    .process(InputImage.fromBitmap(bitmap, 0))
                    .addOnSuccessListener(text -> {
                        if (allowLoginRecovery && clickLoginRecovery(text)) {
                            bitmap.recycle();
                            new Handler(Looper.getMainLooper()).postDelayed(
                                () -> readSaneagoOcr(request, false), 8000
                            );
                            return;
                        }
                        String normalized = text.getText() == null ? "" : text.getText().toLowerCase();
                        if (!allowLoginRecovery && normalized.contains("abrir tela de login")) {
                            bitmap.recycle();
                            reply(request, false, null, "Sessao Saneago expirada; refaca o login no app oficial");
                            return;
                        }
                        try { reply(request, true, parseOcr(text.getText()), null); }
                        catch (Exception error) {
                            String raw = text.getText() == null ? "" : text.getText();
                            String metrics = " chars=" + raw.length() +
                                " conta=" + raw.toLowerCase().contains("conta") +
                                " fatura=" + raw.toLowerCase().contains("fatura") +
                                " consumo=" + raw.toLowerCase().contains("consumo");
                            reply(request, false, null, error.getMessage() + metrics);
                        }
                        finally { bitmap.recycle(); }
                    })
                    .addOnFailureListener(error -> {
                        bitmap.recycle();
                        reply(request, false, null, "OCR indisponivel: " + error.getClass().getSimpleName());
                    });
            }
            @Override public void onFailure(int errorCode) {
                reply(request, false, null, "Screenshot falhou: " + errorCode);
            }
        });
    }

    private boolean clickLoginRecovery(Text text) {
        for (Text.TextBlock block : text.getTextBlocks()) {
            for (Text.Line line : block.getLines()) {
                String value = line.getText().toLowerCase();
                Rect bounds = line.getBoundingBox();
                if (bounds != null && value.contains("abrir tela de login")) {
                    Path path = new Path();
                    path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
                    GestureDescription gesture = new GestureDescription.Builder()
                        .addStroke(new GestureDescription.StrokeDescription(path, 0, 120)).build();
                    return dispatchGesture(gesture, null, null);
                }
            }
        }
        return false;
    }

    /** Instala exclusivamente o WhatsApp oficial já aberto na Play Store. */
    private void installWhatsAppFromPlayStore(String request) {
        Intent market = new Intent(Intent.ACTION_VIEW,
            android.net.Uri.parse("market://details?id=com.whatsapp"))
            .setPackage("com.android.vending")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(market);
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> clickWhatsAppInstall(request, System.currentTimeMillis() + 45_000L), 2500L);
    }

    private void clickWhatsAppInstall(String request, long deadline) {
        AccessibilityNodeInfo root = packageRoot("com.android.vending");
        if (root != null) {
            List<String> text = new ArrayList<>();
            collect(root, text);
            String folded = EquatorialSession.fold(android.text.TextUtils.join(" ", text));
            if (folded.contains("abrir") || folded.contains("desinstalar")) {
                root.recycle();
                reply(request, true, new JSONObject(), null);
                return;
            }
            boolean clicked = gestureClickLabel(root, "instalar");
            root.recycle();
            if (clicked) {
                reply(request, true, new JSONObject(), null);
                return;
            }
        }
        if (System.currentTimeMillis() < deadline) {
            new Handler(Looper.getMainLooper()).postDelayed(
                () -> clickWhatsAppInstall(request, deadline), 1200L);
        } else reply(request, false, null, "Botao Instalar do WhatsApp oficial nao encontrado");
    }

    static JSONObject parseOcr(String raw) throws Exception {
        Map<String, String> values = SaneagoOcrParser.parse(raw);
        JSONObject result = new JSONObject().put("source", "saneago_android_ocr").put("read_only", true);
        for (Map.Entry<String, String> entry : values.entrySet()) result.put(entry.getKey(), entry.getValue());
        return result;
    }

    private AccessibilityNodeInfo saneagoRoot() {
        return packageRoot(SANEAGO);
    }

    private AccessibilityNodeInfo packageRoot(String packageName) {
        AccessibilityNodeInfo active = getRootInActiveWindow();
        if (belongsTo(active, packageName)) return active;
        if (active != null) active.recycle();
        for (AccessibilityWindowInfo window : getWindows()) {
            AccessibilityNodeInfo root = window.getRoot();
            if (belongsTo(root, packageName)) return root;
            if (root != null) root.recycle();
        }
        return null;
    }

    private static boolean belongsToSaneago(AccessibilityNodeInfo root) {
        return belongsTo(root, SANEAGO);
    }

    private static boolean belongsTo(AccessibilityNodeInfo root, String packageName) {
        return root != null && root.getPackageName() != null && packageName.contentEquals(root.getPackageName());
    }

    /**
     * Adapta os passos de artefato a ponte.
     *
     * O fluxo de Pix e boleto vive fora deste arquivo de proposito: usa somente
     * API publica do servico e devolve por callback, entao a integracao e este
     * adaptador e nada mais. Assim o dono do fluxo pode evoluir sem tocar aqui.
     */
    private PixBridge.Reply bridgeReply(final String request) {
        return new PixBridge.Reply() {
            @Override public void ok(JSONObject payload) { reply(request, true, payload, null); }
            @Override public void fail(String error) { reply(request, false, null, error); }
        };
    }

    private void reply(String request, boolean ok, JSONObject payload, String error) {
        if (ok) RodLog.step("resposta", "ok");
        else RodLog.fail("resposta", "falha: " + error);
        getSharedPreferences(PREFS_BRIDGE, MODE_PRIVATE).edit()
            .putString("request_id", request).putBoolean("ok", ok)
            .putString("payload", payload == null ? "{}" : payload.toString())
            .putString("error", error == null ? "" : error.substring(0, Math.min(error.length(), 180))).apply();
    }

    private static void collect(AccessibilityNodeInfo node, List<String> values) {
        CharSequence text = node.getText();
        if (text != null && text.length() > 0) values.add(text.toString().trim());
        CharSequence description = node.getContentDescription();
        if (description != null && description.length() > 0) values.add(description.toString().trim());
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) { collect(child, values); child.recycle(); }
        }
    }
    private static String valueAfterPrefix(List<String> values, String prefix) {
        for (String value : values) if (value.startsWith(prefix)) return value.substring(prefix.length()).trim();
        return "";
    }
    private static String valueAfter(List<String> values, String... labels) {
        for (int i = 0; i < values.size() - 1; i++)
            for (String label : labels) if (values.get(i).equalsIgnoreCase(label)) return values.get(i + 1);
        return "";
    }
}
