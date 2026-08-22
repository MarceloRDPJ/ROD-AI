package br.com.jarviscerrado.poco;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;

final class BillingConfig {
    final JSONObject data;
    BillingConfig(String json) {
        JSONObject parsed;
        try { parsed = new JSONObject(json); } catch (Exception ignored) { parsed = new JSONObject(); }
        data = parsed;
    }
    static BillingConfig load(Context context) { return new BillingConfig(EncryptedSettingsStore.load(context)); }
    boolean saneagoReady() {
        return present("saneago_login") && present("saneago_password") && waterCount() > 0;
    }
    boolean equatorialReady() {
        return digits("equatorial_cpf").length() == 11 &&
            data.optString("equatorial_birth_date", "").matches("\\d{2}/\\d{2}/\\d{4}") && energyCount() > 0;
    }
    int waterCount() { return countSuffix("_water"); }
    int energyCount() { return countSuffix("_energy"); }
    JSONArray waterProperties() { return propertiesWith("_water"); }
    JSONArray energyProperties() { return propertiesWith("_energy"); }
    String value(String key) { return data.optString(key, "").trim(); }
    private boolean present(String key) { return !value(key).isEmpty(); }
    private String digits(String key) { return value(key).replaceAll("\\D", ""); }
    private int countSuffix(String suffix) {
        int count = 0;
        for (String property : new String[]{"kitnet_01", "kitnet_02", "sala_comercial", "casa", "restaurante"})
            if (!value(property + suffix).isEmpty()) count++;
        return count;
    }
    private JSONArray propertiesWith(String suffix) {
        JSONArray result = new JSONArray();
        for (String property : new String[]{"kitnet_01", "kitnet_02", "sala_comercial", "casa", "restaurante"})
            if (!value(property + suffix).isEmpty()) result.put(property);
        return result;
    }
}
