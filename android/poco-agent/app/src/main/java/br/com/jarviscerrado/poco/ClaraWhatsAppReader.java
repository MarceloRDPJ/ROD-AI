package br.com.jarviscerrado.poco;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import java.util.UUID;
import org.json.JSONObject;

/** Consulta somente leitura pelo contato oficial da Clara em Goiás. */
final class ClaraWhatsAppReader {
    private static final long TIMEOUT_MILLIS = 140_000L;
    private ClaraWhatsAppReader() { }

    static JSONObject read(Context context, String property) throws Exception {
        BillingConfig config = BillingConfig.load(context);
        String normalized = EquatorialReader.normalizeProperty(property);
        String unit = digits(config.value(normalized + "_energy"));
        String document = digits(config.value("equatorial_cpf"));
        String birth = config.value("equatorial_birth_date");
        if (unit.isEmpty())
            throw new IllegalStateException("EQUATORIAL_PROPERTY_NOT_MAPPED: imovel sem UC de energia");
        if (document.isEmpty())
            throw new IllegalStateException("EQUATORIAL_CREDENTIALS_MISSING: documento ausente no cofre");

        String request = UUID.randomUUID().toString();
        Intent intent = new Intent(JarvisAccessibilityService.ACTION_BRIDGE)
            .setPackage(context.getPackageName())
            .putExtra("request_id", request)
            .putExtra("operation", "clara_equatorial")
            .putExtra("unit", unit)
            .putExtra("document", document)
            .putExtra("birth", birth);
        context.sendBroadcast(intent);

        SharedPreferences prefs = context.getSharedPreferences(
            JarvisAccessibilityService.PREFS_BRIDGE, Context.MODE_PRIVATE);
        long deadline = System.currentTimeMillis() + TIMEOUT_MILLIS;
        while (System.currentTimeMillis() < deadline) {
            if (request.equals(prefs.getString("request_id", ""))) {
                if (!prefs.getBoolean("ok", false))
                    throw new IllegalStateException(prefs.getString("error", "Falha no canal Clara"));
                return new JSONObject(prefs.getString("payload", "{}"))
                    .put("property", normalized).put("read_only", true)
                    .put("source", "clara_whatsapp");
            }
            Thread.sleep(100L);
        }
        throw new IllegalStateException("EQUATORIAL_PORTAL_TIMEOUT: Clara nao concluiu a conversa");
    }

    private static String digits(String value) {
        return value == null ? "" : value.replaceAll("\\D", "");
    }
}
