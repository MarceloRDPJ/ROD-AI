package br.com.jarviscerrado.poco;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.IBinder;
import android.os.PowerManager;
import java.util.List;
import java.util.Random;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

public class AgentService extends Service {
    /** Healthy polling cadence. Short enough that a Telegram request feels immediate. */
    private static final int POLL_BASE_SECONDS = 15;
    private static final int POLL_MAX_SECONDS = 120;
    /** Heartbeat runs on its own thread so a multi-minute bill query never starves it. */
    private static final int HEARTBEAT_SECONDS = 30;
    /** Must exceed the slowest job (Saneago session recovery) or the CPU sleeps mid-flow. */
    private static final long JOB_WAKELOCK_MILLIS = 300_000L;

    private ScheduledExecutorService heartbeatExecutor;
    private ScheduledExecutorService jobExecutor;
    private JobOutbox outbox;
    private PowerManager.WakeLock jobWakeLock;
    private PowerManager.WakeLock serviceWakeLock;
    private WifiManager.WifiLock wifiLock;
    private final Random jitter = new Random();
    private volatile int failureStreak;
    private volatile boolean busy;

    /**
     * Ponto único de religamento do agente.
     *
     * Devolve {@code false} em vez de estourar. Quem chama daqui em diante é um
     * BroadcastReceiver, e exceção dentro de receiver morre sem deixar rastro
     * legível: se a política de segundo plano do Android 12 recusar o início do
     * serviço em primeiro plano, o que fecha o diagnóstico é o motivo exato na
     * trilha `adb logcat -s ROD`, não um processo derrubado em silêncio.
     */
    public static boolean start(Context context) {
        try {
            context.startForegroundService(new Intent(context, AgentService.class));
            return true;
        } catch (Throwable error) {
            RodLog.fail("autostart", "startForegroundService recusado: " + describe(error));
            return false;
        }
    }

    @Override public void onCreate() {
        super.onCreate();
        NotificationChannel channel = new NotificationChannel("jarvis_agent", "ROD // Poco", NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
        Notification notification = new Notification.Builder(this, "jarvis_agent")
            .setContentTitle("ROD ativo").setContentText("Nó Poco conectado ao Raspberry Pi")
            .setSmallIcon(android.R.drawable.presence_online).build();
        startForeground(41, notification);

        outbox = new JobOutbox(this);
        jobWakeLock = getSystemService(PowerManager.class)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "rod:poco-job");
        jobWakeLock.setReferenceCounted(false);
        serviceWakeLock = getSystemService(PowerManager.class)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "rod:poco-agent");
        serviceWakeLock.setReferenceCounted(false);
        serviceWakeLock.acquire();
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "rod:poco-wifi");
        wifiLock.setReferenceCounted(false);
        wifiLock.acquire();

        heartbeatExecutor = Executors.newSingleThreadScheduledExecutor();
        heartbeatExecutor.scheduleWithFixedDelay(this::heartbeatCycle, 2, HEARTBEAT_SECONDS, TimeUnit.SECONDS);
        jobExecutor = Executors.newSingleThreadScheduledExecutor();
        scheduleNextPoll(1);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) { return START_STICKY; }

    private ApiClient client() {
        String endpoint = getSharedPreferences("agent", MODE_PRIVATE).getString("endpoint", "");
        String secret = SecretStore.load(this);
        if (endpoint.isEmpty() || secret.isEmpty()) return null;
        return new ApiClient(endpoint, secret);
    }

    /**
     * Heartbeat lives on its own executor. Sharing a thread with job execution made the
     * Pi mark the node offline in the middle of the very query it had dispatched.
     */
    private void heartbeatCycle() {
        try {
            ApiClient client = client();
            if (client != null) client.post("/api/poco/heartbeat", heartbeat());
        } catch (Throwable ignored) {
            // A throw here would cancel the scheduled task permanently; swallow it.
        }
    }

    private void scheduleNextPoll(long delaySeconds) {
        if (jobExecutor == null || jobExecutor.isShutdown()) return;
        try { jobExecutor.schedule(this::jobCycle, delaySeconds, TimeUnit.SECONDS); }
        catch (Throwable ignored) { }
    }

    private void jobCycle() {
        long preferred = POLL_BASE_SECONDS;
        try {
            ApiClient client = client();
            if (client == null) return;
            flushOutbox(client);
            JSONObject response = client.get("/api/poco/jobs/next");
            failureStreak = 0;
            if (!response.isNull("job")) {
                runJob(client, response.getJSONObject("job"));
                preferred = 1;
            }
        } catch (Throwable error) {
            if (failureStreak < 6) failureStreak++;
        } finally {
            scheduleNextPoll(nextDelaySeconds(preferred));
        }
    }

    private long nextDelaySeconds(long preferred) {
        if (failureStreak == 0) return preferred;
        long backoff = POLL_BASE_SECONDS * (1L << Math.min(failureStreak, 3));
        return Math.min(POLL_MAX_SECONDS, backoff) + jitter.nextInt(6);
    }

    private void runJob(ApiClient client, JSONObject job) throws Exception {
        String id = job.getString("job_id");
        String action = job.getString("action");
        // The Pi requeues jobs whose lease expired. The result is already durable here.
        RodLog.step("job", "recebido acao=" + action);
        if (outbox.alreadyHandled(id)) { flushOutbox(client); return; }
        busy = true;
        jobWakeLock.acquire(JOB_WAKELOCK_MILLIS);
        try {
            try { client.state(id, "running", null, null); } catch (Exception ignored) { }
            long now = System.currentTimeMillis();
            try {
                JSONObject result = perform(action, job.optJSONObject("params"));
                outbox.record(id, "completed", result.toString(), null, now);
            } catch (Throwable error) {
                RodLog.fail("job", "acao=" + action + " falhou: " + describe(error));
                outbox.record(id, "failed", null, describe(error), now);
            }
            flushOutbox(client);
        } finally {
            if (jobWakeLock.isHeld()) jobWakeLock.release();
            busy = false;
        }
    }

    private JSONObject perform(String action, JSONObject params) throws Exception {
        String property = params == null ? "casa" : params.optString("property", "casa");
        if (action.equals("device_status")) return heartbeat();
        if (action.equals("network_check")) return networkCheck();
        if (action.equals("refresh_saneago_bills")) return cached("saneago", property, SaneagoReader.readCurrent(this, property));
        if (action.equals("refresh_equatorial_bills")) return cached("equatorial", property, EquatorialReader.read(this, property));
        if (action.equals("clara_equatorial_bills")) return cached("equatorial", property, ClaraWhatsAppReader.read(this, property));
        if (action.equals("prepare_clara_whatsapp")) return WhatsAppSetup.prepare(this);
        // Artefatos sob demanda de uma fatura em aberto. Nenhum dos dois paga,
        // confirma ou movimenta nada; e o proprietario quem decide o que fazer com
        // o Pix e com o PDF. Nada disso entra no BillCache: cache guarda leitura,
        // e ordem de pagamento nao pode ficar guardada no telefone.
        if (action.equals("get_equatorial_pix"))
            return ArtifactFlow.pix(this, property, params == null ? "" : params.optString("reference", ""));
        if (action.equals("get_equatorial_boleto"))
            return ArtifactFlow.boleto(this, property, params == null ? "" : params.optString("reference", ""));
        if (action.equals("read_bill_cache")) {
            String provider = params == null ? "" : params.optString("provider", "");
            if (!provider.equals("saneago") && !provider.equals("equatorial"))
                throw new IllegalStateException("Provedor invalido para leitura de cache");
            return BillCache.read(this, provider, property, System.currentTimeMillis());
        }
        throw new IllegalStateException("Acao nao implementada nesta versao do ROD: " + action);
    }

    private JSONObject cached(String provider, String property, JSONObject result) {
        BillCache.store(this, provider, property, result, System.currentTimeMillis());
        return result;
    }

    /**
     * Delivers durable results. A 4xx means the Pi already expired or rejected the job,
     * so the entry is dropped instead of retried forever; a network error keeps it queued.
     */
    private void flushOutbox(ApiClient client) {
        List<JobOutbox.Entry> pending = outbox.pending(20);
        for (JobOutbox.Entry entry : pending) {
            try {
                JSONObject payload = entry.payload == null ? null : new JSONObject(entry.payload);
                client.state(entry.jobId, entry.status, payload, entry.error);
                outbox.delivered(entry.jobId);
            } catch (ApiClient.HttpException http) {
                if (http.permanent()) outbox.delivered(entry.jobId);
                else { outbox.failedAttempt(entry.jobId); break; }
            } catch (Exception offline) {
                outbox.failedAttempt(entry.jobId);
                break;
            }
        }
        outbox.prune(System.currentTimeMillis());
    }

    private static String describe(Throwable error) {
        String message = error.getClass().getSimpleName();
        if (error.getMessage() != null) message += ": " + error.getMessage();
        return message.substring(0, Math.min(message.length(), 180));
    }

    private JSONObject networkCheck() throws Exception {
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        NetworkCapabilities caps = cm.getNetworkCapabilities(cm.getActiveNetwork());
        return new JSONObject()
            .put("wifi_connected", caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))
            .put("internet_validated", caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
            .put("internet_capable", caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET));
    }

    private JSONObject heartbeat() throws Exception {
        Intent battery = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int level = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int temperature = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1);
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        NetworkCapabilities caps = cm.getNetworkCapabilities(cm.getActiveNetwork());
        boolean wifi = caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
        BillingConfig billing = BillingConfig.load(this);
        return new JSONObject().put("node_id", "poco-x3-nfc").put("battery_level", level)
            .put("battery_temperature_c", temperature / 10.0).put("thermal_status", "android")
            .put("wifi_connected", wifi)
            // O versionName sozinho nao distingue binario: dois builds locais
            // diferentes carregam o mesmo "1.0.2", e o Pi passa a relatar uma
            // versao que nao identifica o que esta rodando. O versionCode e o
            // numero que muda a cada APK, entao e ele que fecha a rastreabilidade.
            .put("agent_version", BuildConfig.VERSION_NAME)
            .put("agent_version_code", BuildConfig.VERSION_CODE)
            .put("saneago_configured", billing.saneagoReady())
            .put("equatorial_configured", billing.equatorialReady())
            .put("water_units", billing.waterCount()).put("energy_units", billing.energyCount())
            // Somente nomes lógicos, nunca UC, CPF ou credencial. O Pi precisa
            // destes nomes para montar o menu completo antes da primeira leitura.
            .put("water_properties", billing.waterProperties())
            .put("energy_properties", billing.energyProperties())
            .put("busy", busy)
            .put("pending_results", outbox == null ? 0 : outbox.pendingCount());
    }

    @Override public void onDestroy() {
        if (heartbeatExecutor != null) heartbeatExecutor.shutdownNow();
        if (jobExecutor != null) jobExecutor.shutdownNow();
        if (jobWakeLock != null && jobWakeLock.isHeld()) jobWakeLock.release();
        if (serviceWakeLock != null && serviceWakeLock.isHeld()) serviceWakeLock.release();
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        if (outbox != null) outbox.close();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
