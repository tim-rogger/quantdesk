package ch.quantdesk.web;

import ch.quantdesk.universe.ScanResult;
import ch.quantdesk.universe.UniverseScanner;
import java.time.Instant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ScanController {

    private final UniverseScanner scanner;

    public ScanController(UniverseScanner scanner) {
        this.scanner = scanner;
    }

    @GetMapping("/scan")
    public ScanResponse scan() {
        return new ScanResponse(scanner.lastScanAt(), scanner.getLastScan());
    }

    @PostMapping("/scan/run")
    public ScanResponse run() {
        List<ScanResult> results = scanner.scan();
        return new ScanResponse(scanner.lastScanAt(), results);
    }

    public record ScanResponse(Instant lastScanAt, List<ScanResult> results) {
    }
}
