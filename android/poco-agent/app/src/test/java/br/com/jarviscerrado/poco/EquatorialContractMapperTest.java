package br.com.jarviscerrado.poco;

import static org.junit.Assert.assertEquals;
import java.util.Arrays;
import org.junit.Test;

public class EquatorialContractMapperTest {
    @Test public void mapsLegacyAliasToCurrentContract() throws Exception {
        assertEquals("000000000000111",
            EquatorialContractMapper.resolveAccounts(Arrays.asList(
                new EquatorialContractMapper.Account("000000000000111", "12345678")),
                "12345678", Arrays.asList()));
    }

    @Test public void acceptsUppercaseJwtShapeAndLeadingZeros() throws Exception {
        assertEquals("000000000000222",
            EquatorialContractMapper.resolveAccounts(Arrays.asList(
                new EquatorialContractMapper.Account("000000000000222", "00012345678")),
                "12345678", Arrays.asList()));
    }

    @Test public void mapsOnlyRemainingOfficialContract() throws Exception {
        assertEquals("000000000000303", EquatorialContractMapper.resolveAccounts(Arrays.asList(
            new EquatorialContractMapper.Account("000000000000101"),
            new EquatorialContractMapper.Account("000000000000202"),
            new EquatorialContractMapper.Account("000000000000303")),
            "12345678", Arrays.asList("000000000000101", "000000000000202")));
    }

    @Test public void rejectsSubstringAndAmbiguousRemainder() throws Exception {
        assertEquals("", EquatorialContractMapper.resolveAccounts(Arrays.asList(
            new EquatorialContractMapper.Account("000000012345678"),
            new EquatorialContractMapper.Account("000000000000999")),
            "12345678", Arrays.asList()));
    }

    @Test public void rejectsTwoDirectMatches() throws Exception {
        assertEquals("", EquatorialContractMapper.resolveAccounts(Arrays.asList(
            new EquatorialContractMapper.Account("000000000000111", "12345678"),
            new EquatorialContractMapper.Account("000000000000222", "12345678")),
            "12345678", Arrays.asList()));
    }
}
