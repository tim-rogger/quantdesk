package ch.quantdesk.universe;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class CsvUniverseProviderTest {

    private final CsvUniverseProvider provider = new CsvUniverseProvider(new UniverseProperties());

    @Test
    void bundledUniverseLoadsLargeCleanSymbolList() {
        List<String> symbols = provider.symbols();

        assertThat(symbols).hasSizeGreaterThanOrEqualTo(80);
        assertThat(symbols).allSatisfy(symbol -> {
            assertThat(symbol).isNotBlank();
            assertThat(symbol).endsWith(".us");
        });
    }

    @Test
    void bundledUniverseContainsMegacaps() {
        List<String> symbols = provider.symbols();

        assertThat(symbols).contains("aapl.us", "msft.us", "nvda.us", "brk-b.us");
        assertThat(symbols).doesNotHaveDuplicates();
    }
}
