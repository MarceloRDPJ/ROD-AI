package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.Test;

/**
 * Religamento automático do agente, sem ninguém tocar na tela.
 *
 * O Poco é servidor doméstico. Depois de um `adb install -r` o processo do app
 * morre, e antes desta mudança nada o trazia de volta: o nó ficava offline no Pi
 * até alguém abrir o aplicativo com a mão. Servidor que precisa de dedo humano
 * para voltar não é servidor.
 *
 * Metade deste arquivo testa lógica; a outra metade testa o manifesto, porque
 * neste defeito o manifesto *é* o mecanismo. Um filtro de intent sem a ação
 * certa não quebra compilação, não lança exceção e não aparece em log: ele
 * simplesmente nunca dispara. Só o arquivo declarado pode provar isso antes do
 * aparelho.
 */
public class AgentAutostartTest {

    // -------------------------------------------------- quais ações religam

    @Test
    public void rebootAndReinstallAreBothCausesToBringTheAgentBack() {
        assertTrue(BootReceiver.shouldStartAgent("android.intent.action.BOOT_COMPLETED"));
        assertTrue(BootReceiver.shouldStartAgent("android.intent.action.MY_PACKAGE_REPLACED"));
    }

    @Test
    public void anotherAppBeingUpdatedIsNotAReasonToTouchTheAgent() {
        // PACKAGE_REPLACED fala de terceiros (Chrome, Saneago, Equatorial).
        // Confundi-lo com MY_PACKAGE_REPLACED faria o agente reiniciar no meio
        // de uma consulta só porque o Chrome se atualizou.
        assertFalse(BootReceiver.shouldStartAgent("android.intent.action.PACKAGE_REPLACED"));
        assertFalse(BootReceiver.shouldStartAgent("android.intent.action.PACKAGE_ADDED"));
        assertFalse(BootReceiver.shouldStartAgent("android.intent.action.MY_PACKAGE_SUSPENDED"));
        assertFalse(BootReceiver.shouldStartAgent("android.intent.action.SCREEN_ON"));
    }

    @Test
    public void aBroadcastWithoutActionIsIgnoredInsteadOfCrashingTheReceiver() {
        assertFalse(BootReceiver.shouldStartAgent(null));
        assertFalse(BootReceiver.shouldStartAgent(""));
    }

    @Test
    public void theTrailNamesTheCauseSoTheDeviceRunIsReadable() {
        assertEquals("boot", BootReceiver.cause("android.intent.action.BOOT_COMPLETED"));
        assertEquals("reinstalacao", BootReceiver.cause("android.intent.action.MY_PACKAGE_REPLACED"));
        assertEquals("acao-ignorada", BootReceiver.cause("android.intent.action.SCREEN_ON"));
        assertEquals("acao-ignorada", BootReceiver.cause(null));
    }

    // -------------------------------------------------- o manifesto declarado

    @Test
    public void theReceiverListensForReinstallAndNotOnlyForBoot() throws IOException {
        String filter = bootReceiverBlock();
        assertTrue("BOOT_COMPLETED continua cobrindo o reboot",
            filter.contains("android.intent.action.BOOT_COMPLETED"));
        assertTrue("MY_PACKAGE_REPLACED cobre a reinstalacao; sem ele o no fica offline",
            filter.contains("android.intent.action.MY_PACKAGE_REPLACED"));
    }

    @Test
    public void theReceiverFilterCarriesNoDataElementThatWouldSilenceIt() throws IOException {
        // MY_PACKAGE_REPLACED é entregue direto ao pacote substituído e não leva
        // dados. Um <data> no filtro não dá erro nenhum: apenas faz o filtro
        // nunca casar, e o defeito voltaria idêntico e silencioso.
        assertFalse("filtro do BootReceiver nao deve ter elemento de dados",
            bootReceiverBlock().contains("<data"));
    }

    @Test
    public void theBootPermissionIsStillDeclared() throws IOException {
        assertTrue(manifest().contains("android.permission.RECEIVE_BOOT_COMPLETED"));
    }

    @Test
    public void theAgentIsStillAForegroundDataSyncService() throws IOException {
        // Sem serviço em primeiro plano o Android 12 mata o agente em minutos, e
        // o heartbeat morre com ele.
        String text = manifest();
        assertTrue(text.contains("android:name=\".AgentService\""));
        assertTrue(text.contains("android:foregroundServiceType=\"dataSync\""));
        assertTrue(text.contains("android.permission.FOREGROUND_SERVICE_DATA_SYNC"));
    }

    @Test
    public void theAppIsDeliberatelyNotDirectBootAware() throws IOException {
        // Tentador para subir antes do desbloqueio, e errado aqui: em Direct Boot
        // o armazenamento cifrado por credencial não está montado, e é lá que
        // vivem billing_secure, bill_cache_secure e agent_secure. O agente
        // acordaria cego, sem endpoint e sem segredo, e trocaria o diagnóstico
        // real por uma falha inventada. Se o boot não religar o nó, a causa se
        // investiga no aparelho; não se contorna sacrificando o cofre.
        assertFalse(manifest().contains("directBootAware"));
    }

    @Test
    public void theChannelsTheReadersNeedAreStillVisibleToTheApp() throws IOException {
        // Guarda de vizinhança: o manifesto é compartilhado. Remover uma destas
        // linhas cegaria os leitores de conta sem quebrar nada em tempo de build.
        String text = manifest();
        assertTrue(text.contains("br.com.saneago"));
        assertTrue(text.contains("com.android.chrome"));
        assertTrue(text.contains("com.equatorialenergia"));
        assertTrue(text.contains("android.permission.BIND_ACCESSIBILITY_SERVICE"));
    }

    @Test
    public void theAccessibilityServiceStillWatchesTheThreePackages() throws IOException {
        String text = read(new File(moduleRoot(), "src/main/res/xml/accessibility_service.xml"));
        assertTrue(text.contains("br.com.saneago"));
        assertTrue(text.contains("com.android.chrome"));
        assertTrue(text.contains("com.equatorialenergia"));
    }

    // -------------------------------------------------- versionamento

    @Test
    public void aDifferentApkCarriesADifferentVersionCode() throws IOException {
        // Reinstalar binário diferente sob o mesmo versionCode apaga a
        // rastreabilidade do que está de fato no aparelho: o inventário passa a
        // relatar um código que não corresponde ao que roda.
        String gradle = read(new File(moduleRoot(), "build.gradle"));
        int code = versionCode(gradle);
        assertTrue("versionCode deve passar de 32; encontrado " + code, code >= 33);
        assertFalse("versionName 1.0.0 pertence ao APK anterior",
            gradle.contains("versionName '1.0.0'"));
        // 33 ja foi commitado. Mudanca de comportamento depois dele precisa de
        // numero proprio, ou dois binarios diferentes respondem "33" ao Pi.
        assertTrue("mudanca posterior a 33 exige versionCode proprio", code >= 34);
    }

    private static int versionCode(String gradle) {
        Matcher matcher = Pattern.compile("(?m)^\\s*versionCode\\s+(\\d+)").matcher(gradle);
        assertTrue("versionCode nao encontrado em build.gradle", matcher.find());
        return Integer.parseInt(matcher.group(1));
    }

    // -------------------------------------------------- segundo caminho

    @Test
    public void theAccessibilityRebindAlsoBringsTheAgentBack() throws IOException {
        // O sistema religa o servico de acessibilidade sozinho apos reinstalacao
        // e apos boot — foi o que o dumpsys mostrou com o no offline: a
        // acessibilidade de pe e o agente ausente. Esse bind nao passa pelo
        // autostart da MIUI, que e toggle por app e nao se concede por codigo.
        String source = read(new File(moduleRoot(),
            "src/main/java/br/com/jarviscerrado/poco/JarvisAccessibilityService.java"));
        int hook = source.indexOf("onServiceConnected()");
        assertTrue("onServiceConnected nao declarado", hook >= 0);
        int next = source.indexOf("onAccessibilityEvent", hook);
        assertTrue("corpo de onServiceConnected nao localizado", next > hook);
        assertTrue("o rebind precisa religar o agente",
            source.substring(hook, next).contains("AgentService.start(this)"));
    }

    @Test
    public void theSecondPathIsABeltNotAReplacement() throws IOException {
        // Se alguem trocar um mecanismo pelo outro, sobra um caminho so, e o
        // caminho que sobrar sera o que a MIUI sabe bloquear.
        assertTrue(bootReceiverBlock().contains("android.intent.action.MY_PACKAGE_REPLACED"));
    }

    // -------------------------------------------------- identidade do binario

    @Test
    public void theHeartbeatCarriesTheVersionCodeAndNotOnlyTheName() throws IOException {
        // O versionName e o unico identificador que chegava ao Pi, e dois builds
        // locais diferentes carregam o mesmo nome: o inventario relatava uma
        // versao que nao dizia qual binario estava no telefone.
        String source = read(new File(moduleRoot(),
            "src/main/java/br/com/jarviscerrado/poco/AgentService.java"));
        assertTrue("agent_version continua sendo enviado",
            source.contains("\"agent_version\", BuildConfig.VERSION_NAME"));
        assertTrue("agent_version_code precisa ir no heartbeat",
            source.contains("\"agent_version_code\", BuildConfig.VERSION_CODE"));
    }

    @Test
    public void theNumberTheHeartbeatSendsIsTheNumberTheBuildDeclares() throws IOException {
        // Trava de deriva: BuildConfig e gerado a partir do build.gradle, e o
        // heartbeat manda BuildConfig. Se os dois se separarem, o Pi passa a
        // gravar um numero que nao corresponde ao APK.
        assertEquals(versionCode(read(new File(moduleRoot(), "build.gradle"))),
            BuildConfig.VERSION_CODE);
        assertEquals("1.0.35", BuildConfig.VERSION_NAME);
    }

    // -------------------------------------------------- leitura dos arquivos

    /** Trecho do manifesto de `.BootReceiver` até o fechamento do receiver. */
    private static String bootReceiverBlock() throws IOException {
        String text = manifest();
        int start = text.indexOf("android:name=\".BootReceiver\"");
        assertTrue("BootReceiver nao declarado no manifesto", start >= 0);
        int end = text.indexOf("</receiver>", start);
        assertTrue("receiver sem fechamento", end > start);
        return text.substring(start, end);
    }

    private static String manifest() throws IOException {
        return read(new File(moduleRoot(), "src/main/AndroidManifest.xml"));
    }

    private static String read(File file) throws IOException {
        assertTrue("arquivo esperado nao existe: " + file, file.isFile());
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    /**
     * Raiz do módulo `app`, independente de onde o Gradle ancore o diretório de
     * trabalho do teste.
     */
    private static File moduleRoot() {
        File here = new File("").getAbsoluteFile();
        String[] candidates = {".", "app", "android/poco-agent/app"};
        for (String candidate : candidates) {
            File dir = new File(here, candidate);
            if (new File(dir, "src/main/AndroidManifest.xml").isFile()) return dir;
        }
        for (File dir = here; dir != null; dir = dir.getParentFile()) {
            if (new File(dir, "src/main/AndroidManifest.xml").isFile()) return dir;
        }
        throw new IllegalStateException("modulo app nao localizado a partir de " + here);
    }
}
