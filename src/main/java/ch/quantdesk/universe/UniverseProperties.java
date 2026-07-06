package ch.quantdesk.universe;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "quantdesk.universe")
public class UniverseProperties {

    private String file = "data/universe.csv";
    private int topN = 5;
    private int lookbackDays = 63;
    private long scanIntervalMs = 900000L;
    private long scanInitialDelayMs = 15000L;

    public String getFile() {
        return file;
    }

    public void setFile(String file) {
        this.file = file;
    }

    public int getTopN() {
        return topN;
    }

    public void setTopN(int topN) {
        this.topN = topN;
    }

    public int getLookbackDays() {
        return lookbackDays;
    }

    public void setLookbackDays(int lookbackDays) {
        this.lookbackDays = lookbackDays;
    }

    public long getScanIntervalMs() {
        return scanIntervalMs;
    }

    public void setScanIntervalMs(long scanIntervalMs) {
        this.scanIntervalMs = scanIntervalMs;
    }

    public long getScanInitialDelayMs() {
        return scanInitialDelayMs;
    }

    public void setScanInitialDelayMs(long scanInitialDelayMs) {
        this.scanInitialDelayMs = scanInitialDelayMs;
    }
}
