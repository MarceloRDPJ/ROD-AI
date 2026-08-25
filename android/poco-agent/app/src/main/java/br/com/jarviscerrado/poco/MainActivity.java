package br.com.jarviscerrado.poco;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.BatteryManager;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Base64;
import android.view.Gravity;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.concurrent.Executors;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private TextView linkValue;
    private TextView nodeValue;
    private TextView batteryValue;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        provisionForDevelopment();
        if (!SecretStore.load(this).isEmpty()) AgentService.start(this);
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 10);
        }
        setContentView(buildDashboard());
        refreshStatus();
        // Entrada operacional usada pelo PC administrador: só abre a ficha
        // oficial da Play Store e aciona a preparação já limitada no agente.
        prepareClaraIfRequested(getIntent());
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        prepareClaraIfRequested(intent);
    }

    private void prepareClaraIfRequested(Intent intent) {
        if (intent != null && intent.getBooleanExtra("prepare_clara", false)) {
            Executors.newSingleThreadExecutor().execute(() -> {
                try { WhatsAppSetup.prepare(getApplicationContext()); }
                catch (Exception error) { RodLog.fail("clara", error.getMessage()); }
            });
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (linkValue != null) refreshStatus();
    }

    private ScrollView buildDashboard() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout root = RodUi.screen(this);

        LinearLayout brand = new LinearLayout(this);
        brand.setOrientation(LinearLayout.HORIZONTAL);
        brand.setGravity(Gravity.CENTER_VERTICAL);
        ImageView logo = new ImageView(this);
        logo.setImageResource(br.com.jarviscerrado.poco.R.drawable.rdp_logo);
        logo.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
        brand.addView(logo, new LinearLayout.LayoutParams(RodUi.dp(this, 58), RodUi.dp(this, 58)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(RodUi.dp(this, 14), 0, 0, 0);
        names.addView(RodUi.text(this, "ROD", 30, Color.WHITE, true));
        names.addView(RodUi.label(this, "RDP STUDIO // HOME OPERATIONS"));
        brand.addView(names, new LinearLayout.LayoutParams(0, -2, 1));
        root.addView(brand);

        TextView intro = RodUi.text(this,
            "Nó móvel dedicado para rede, contas e automações da casa.", 16, RodUi.MUTED, false);
        intro.setPadding(0, RodUi.dp(this, 18), 0, RodUi.dp(this, 18));
        root.addView(intro);

        LinearLayout statusCard = RodUi.card(this);
        statusCard.addView(RodUi.label(this, "OPERAÇÃO AGORA"));
        linkValue = RodUi.metric(this, "Verificando Pi…");
        statusCard.addView(linkValue);
        nodeValue = RodUi.text(this, "Agente local iniciando", 14, RodUi.MUTED, false);
        statusCard.addView(nodeValue);
        batteryValue = RodUi.text(this, "Bateria: lendo", 14, RodUi.MUTED, false);
        statusCard.addView(batteryValue);
        root.addView(statusCard, RodUi.cardParams(this));

        root.addView(RodUi.section(this, "CENTRAL ROD"));
        root.addView(action("▤", "Contas e faturas", "Saneago, Equatorial e imóveis", v ->
            startActivity(new Intent(this, BillingSettingsActivity.class))));
        root.addView(action("⌁", "Conexão com o Pi", "Estado do nó e canal protegido", v ->
            startActivity(new Intent(this, ConnectionSettingsActivity.class))));
        root.addView(action("◎", "Automação local", "Permissão usada nas consultas", v ->
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))));

        root.addView(RodUi.section(this, "CAPACIDADES ATIVAS"));
        LinearLayout capabilities = RodUi.card(this);
        capabilities.addView(RodUi.statusRow(this, "Rede pelo Poco", "Validação Android", RodUi.GREEN));
        capabilities.addView(RodUi.statusRow(this, "Saneago", "Leitura local assistida", RodUi.AMBER));
        capabilities.addView(RodUi.statusRow(this, "Equatorial", "Leitura assistida / CAPTCHA", RodUi.AMBER));
        capabilities.addView(RodUi.statusRow(this, "Telegram", "Controlado pelo Pi", RodUi.CYAN));
        root.addView(capabilities, RodUi.cardParams(this));

        TextView footer = RodUi.label(this,
            "ROD " + BuildConfig.VERSION_NAME + " // RDP STUDIO // DADOS LOCAIS");
        footer.setGravity(Gravity.CENTER);
        footer.setPadding(0, RodUi.dp(this, 30), 0, RodUi.dp(this, 20));
        root.addView(footer);
        scroll.addView(root);
        return scroll;
    }

    private LinearLayout action(String glyph, String title, String subtitle,
                                android.view.View.OnClickListener click) {
        LinearLayout card = RodUi.card(this);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setGravity(Gravity.CENTER_VERTICAL);
        RodUi.makeInteractive(this, card);
        card.setOnClickListener(click);
        TextView icon = RodUi.icon(this, glyph);
        card.addView(icon, new LinearLayout.LayoutParams(RodUi.dp(this, 44), RodUi.dp(this, 44)));
        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(RodUi.dp(this, 14), 0, RodUi.dp(this, 10), 0);
        copy.addView(RodUi.text(this, title, 17, Color.WHITE, true));
        TextView sub = RodUi.text(this, subtitle, 13, RodUi.MUTED, false);
        sub.setPadding(0, RodUi.dp(this, 5), 0, 0);
        copy.addView(sub);
        card.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
        TextView arrow = RodUi.text(this, "›", 27, RodUi.ACCENT, false);
        arrow.setGravity(Gravity.CENTER);
        card.addView(arrow, new LinearLayout.LayoutParams(RodUi.dp(this, 24), -1));
        card.setLayoutParams(RodUi.cardParams(this));
        return card;
    }

    private void refreshStatus() {
        Intent battery = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int level = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int temp = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1);
        batteryValue.setText(String.format("Poco: %d%% // %.1f °C", level, temp / 10.0));
        boolean configured = !SecretStore.load(this).isEmpty();
        nodeValue.setText(configured ? "Agente protegido pelo Android Keystore" : "Conexão ainda não configurada");
        if (!configured) { linkValue.setText("PI NÃO CONFIGURADO"); return; }
        Executors.newSingleThreadExecutor().execute(() -> {
            try {
                String endpoint = getSharedPreferences("agent", MODE_PRIVATE).getString("endpoint", "");
                JSONObject response = new ApiClient(endpoint, SecretStore.load(this)).get("/api/poco/status");
                runOnUiThread(() -> linkValue.setText(response.optBoolean("online") ? "PI + POCO ONLINE" : "PI ONLINE // POCO SINCRONIZANDO"));
            } catch (Exception error) {
                runOnUiThread(() -> linkValue.setText("PI FORA DE ALCANCE"));
            }
        });
    }

    private void provisionForDevelopment() {
        if (!BuildConfig.DEBUG) return;
        try {
            if (getIntent().hasExtra("provision_secret")) {
                String endpoint = getIntent().getStringExtra("provision_endpoint");
                String secret = getIntent().getStringExtra("provision_secret");
                if (endpoint != null && secret != null && secret.length() >= 32) {
                    getSharedPreferences("agent", MODE_PRIVATE).edit().putString("endpoint", endpoint).apply();
                    SecretStore.save(this, secret);
                    getIntent().removeExtra("provision_secret");
                    AgentService.start(this);
                }
            }
            String encodedBilling = getIntent().getStringExtra("provision_billing_b64");
            if (encodedBilling != null && !encodedBilling.isEmpty()) {
                JSONObject current = new JSONObject(EncryptedSettingsStore.load(this));
                JSONObject incoming = new JSONObject(new String(
                    Base64.decode(encodedBilling, Base64.NO_WRAP), StandardCharsets.UTF_8
                ));
                Iterator<String> keys = incoming.keys();
                while (keys.hasNext()) {
                    String key = keys.next();
                    current.put(key, incoming.getString(key));
                }
                EncryptedSettingsStore.save(this, current.toString());
                getIntent().removeExtra("provision_billing_b64");
            }
        } catch (Exception ignored) { }
    }
}
