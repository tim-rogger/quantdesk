package ch.quantdesk;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class QuantDeskApplication {

    public static void main(String[] args) {
        SpringApplication.run(QuantDeskApplication.class, args);
    }
}
