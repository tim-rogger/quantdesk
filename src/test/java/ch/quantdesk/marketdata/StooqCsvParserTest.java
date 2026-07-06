package ch.quantdesk.marketdata;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.LocalDate;
import org.junit.jupiter.api.Test;

class StooqCsvParserTest {

    private static final String FIXTURE = """
            Date,Open,High,Low,Close,Volume
            2024-01-02,185.50,187.20,184.10,186.30,52000000
            2024-01-03,186.00,188.40,185.60,187.90,48000000
            2024-01-05,189.10,190.00,187.30,188.20,51000000
            2024-01-04,187.50,189.80,186.90,189.40,47500000
            not-a-date,1.00,2.00,0.50,1.50,100
            2024-01-08,188.00,189.50,186.70,187.10,46000000
            2024-01-09,187.30,188.90,185.40,186.00,49000000
            2024-01-10,186.50,abc,185.00,186.80,45000000
            2024-01-10,186.50,188.10,185.00,186.80,45000000
            2024-01-11,187.00,190.20,186.40,189.90
            2024-01-11,187.00,190.20,186.40,189.90,53000000

            2024-01-12,190.00,191.50,188.80,191.20,55000000
            2024-01-15,191.00,192.70,189.60,190.10,50000000
            """;

    @Test
    void parsesValidRowsAndSkipsMalformedOnes() {
        BarSeries series = StooqCsvParser.parse("AAPL.US", FIXTURE);

        assertThat(series.symbol()).isEqualTo("AAPL.US");
        assertThat(series.size()).isEqualTo(10);
    }

    @Test
    void parsesFieldValues() {
        BarSeries series = StooqCsvParser.parse("AAPL.US", FIXTURE);

        Bar first = series.get(0);
        assertThat(first.date()).isEqualTo(LocalDate.of(2024, 1, 2));
        assertThat(first.open()).isEqualByComparingTo(new BigDecimal("185.50"));
        assertThat(first.high()).isEqualByComparingTo(new BigDecimal("187.20"));
        assertThat(first.low()).isEqualByComparingTo(new BigDecimal("184.10"));
        assertThat(first.close()).isEqualByComparingTo(new BigDecimal("186.30"));
        assertThat(first.volume()).isEqualTo(52000000L);

        Bar last = series.get(series.size() - 1);
        assertThat(last.date()).isEqualTo(LocalDate.of(2024, 1, 15));
        assertThat(last.close()).isEqualByComparingTo(new BigDecimal("190.10"));
    }

    @Test
    void ordersBarsChronologically() {
        BarSeries series = StooqCsvParser.parse("AAPL.US", FIXTURE);

        for (int index = 1; index < series.size(); index++) {
            assertThat(series.get(index).date()).isAfter(series.get(index - 1).date());
        }
        assertThat(series.get(2).date()).isEqualTo(LocalDate.of(2024, 1, 4));
        assertThat(series.get(3).date()).isEqualTo(LocalDate.of(2024, 1, 5));
    }

    @Test
    void returnsEmptySeriesForHeaderOnlyCsv() {
        BarSeries series = StooqCsvParser.parse("AAPL.US", "Date,Open,High,Low,Close,Volume\n");

        assertThat(series.size()).isZero();
    }
}
