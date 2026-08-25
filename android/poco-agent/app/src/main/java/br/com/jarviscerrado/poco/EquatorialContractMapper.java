package br.com.jarviscerrado.poco;

import java.util.HashSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Reconciles an official Equatorial account list without guessing identifiers. */
final class EquatorialContractMapper {
    private static final String[] NUMBER_KEYS = {"Numero", "numero"};
    private static final String[] ALIAS_KEYS = {
        "Numero", "numero", "NumeroInstalacao", "numeroInstalacao",
        "Uc", "uc", "UcMascarada", "ucMascarada", "CodigoUnico", "codigoUnico"
    };

    private EquatorialContractMapper() { }

    static final class Account {
        final String number;
        final String[] aliases;
        Account(String number, String... aliases) {
            this.number = number == null ? "" : number.replaceAll("\\D", "");
            this.aliases = aliases == null ? new String[0] : aliases;
        }
    }

    static String resolve(String accountsJson, String legacyUnit, List<String> knownUnits)
            throws Exception {
        JSONArray json = new JSONArray(accountsJson == null ? "[]" : accountsJson);
        List<Account> accounts = new ArrayList<>();
        for (int i = 0; i < json.length(); i++) {
            JSONObject object = json.optJSONObject(i);
            if (object == null) continue;
            String[] aliases = new String[ALIAS_KEYS.length];
            for (int j = 0; j < ALIAS_KEYS.length; j++)
                aliases[j] = object.optString(ALIAS_KEYS[j], "");
            accounts.add(new Account(first(object, NUMBER_KEYS), aliases));
        }
        return resolveAccounts(accounts, legacyUnit, knownUnits);
    }

    static String resolveAccounts(List<Account> accounts, String legacyUnit,
                                  List<String> knownUnits) {
        String legacy = canonical(legacyUnit);
        if (legacy.isEmpty()) return "";

        Set<String> direct = new HashSet<>();
        for (Account account : accounts) {
            String number = account.number;
            if (number.isEmpty()) continue;
            for (String alias : account.aliases) if (same(alias, legacy)) direct.add(number);
        }
        if (direct.size() == 1) return direct.iterator().next();
        if (direct.size() > 1) return "";

        // The migration may omit the former identifier. In that case we only
        // accept set reconciliation: all configured current contracts are
        // removed and exactly one official contract must remain. More than one
        // means ambiguity and is deliberately rejected.
        Set<String> known = new HashSet<>();
        if (knownUnits != null) {
            for (String value : knownUnits) {
                String raw = value == null ? "" : value.replaceAll("\\D", "");
                String digits = canonical(value);
                if (raw.length() > 8) known.add(digits);
            }
        }
        Set<String> remaining = new HashSet<>();
        for (Account account : accounts) {
            String number = account.number;
            if (!number.isEmpty() && !known.contains(canonical(number))) remaining.add(number);
        }
        return remaining.size() == 1 ? remaining.iterator().next() : "";
    }

    private static String first(JSONObject object, String[] keys) {
        for (String key : keys) {
            String value = object.optString(key, "").replaceAll("\\D", "");
            if (!value.isEmpty()) return value;
        }
        return "";
    }

    private static boolean same(String left, String right) {
        String a = canonical(left);
        String b = canonical(right);
        return !a.isEmpty() && a.equals(b);
    }

    private static String canonical(String value) {
        String digits = value == null ? "" : value.replaceAll("\\D", "");
        return digits.replaceFirst("^0+(?!$)", "");
    }
}
