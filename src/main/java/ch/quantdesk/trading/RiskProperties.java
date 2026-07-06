package ch.quantdesk.trading;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "quantdesk.risk")
public class RiskProperties {

    private double maxPositionFraction = 0.2;
    private double maxDailyLossPct = 2.0;

    public double getMaxPositionFraction() {
        return maxPositionFraction;
    }

    public void setMaxPositionFraction(double maxPositionFraction) {
        this.maxPositionFraction = maxPositionFraction;
    }

    public double getMaxDailyLossPct() {
        return maxDailyLossPct;
    }

    public void setMaxDailyLossPct(double maxDailyLossPct) {
        this.maxDailyLossPct = maxDailyLossPct;
    }
}
