package br.com.jarviscerrado.poco;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import org.json.JSONObject;
import java.util.UUID;

/** Prepara o canal oficial da Clara sem baixar APK de terceiros. */
final class WhatsAppSetup {
    private static final String PACKAGE = "com.whatsapp";
    private WhatsAppSetup() { }

    static JSONObject prepare(Context context) throws Exception {
        if (!installed(context)) installFromPlayStore(context);
        long deadline = System.currentTimeMillis() + 180_000L;
        while (!installed(context) && System.currentTimeMillis() < deadline) Thread.sleep(1000L);
        if (!installed(context))
            throw new IllegalStateException("EQUATORIAL_WHATSAPP_NOT_INSTALLED: a Play Store nao concluiu a instalacao");

        Intent launch = context.getPackageManager().getLaunchIntentForPackage(PACKAGE);
        if (launch == null)
            throw new IllegalStateException("EQUATORIAL_WHATSAPP_NOT_INSTALLED: pacote sem atividade inicial");
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(launch);
        Thread.sleep(1400L);
        JSONObject companion = callAccessibility(context, "setup_whatsapp_companion", 100_000L);
        return new JSONObject().put("installed", true).put("official_package", PACKAGE)
            .put("qr_ready", companion.optBoolean("qr_ready", false));
    }

    private static void installFromPlayStore(Context context) throws Exception {
        callAccessibility(context, "install_whatsapp", 55_000L);
    }

    private static JSONObject callAccessibility(Context context, String operation, long timeout) throws Exception {
        String request = UUID.randomUUID().toString();
        Intent intent = new Intent(JarvisAccessibilityService.ACTION_BRIDGE)
            .setPackage(context.getPackageName())
            .putExtra("request_id", request)
            .putExtra("operation", operation);
        context.sendBroadcast(intent);
        SharedPreferences prefs = context.getSharedPreferences(
            JarvisAccessibilityService.PREFS_BRIDGE, Context.MODE_PRIVATE);
        long deadline = System.currentTimeMillis() + timeout;
        while (System.currentTimeMillis() < deadline) {
            if (request.equals(prefs.getString("request_id", ""))) {
                if (!prefs.getBoolean("ok", false))
                    throw new IllegalStateException(prefs.getString("error", "Falha na Play Store"));
                return new JSONObject(prefs.getString("payload", "{}"));
            }
            Thread.sleep(100L);
        }
        throw new IllegalStateException("EQUATORIAL_WHATSAPP_SETUP_TIMEOUT: a ponte nao respondeu");
    }

    private static boolean installed(Context context) {
        try {
            // O Poco está em Android 12: PackageInfoFlags só existe a partir da
            // API 33 e causava NoSuchMethodError antes mesmo de abrir a loja.
            context.getPackageManager().getPackageInfo(PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException ignored) {
            return false;
        }
    }
}
