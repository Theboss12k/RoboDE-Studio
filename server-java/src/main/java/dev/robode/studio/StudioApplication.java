package dev.robode.studio;

import dev.robode.studio.config.BindPolicy;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class StudioApplication {
    public static void main(String[] args) {
        // refuse early (before Spring starts) when the environment asks for a non-loopback bind without a token
        String host = System.getenv().getOrDefault("STUDIO_HOST", "127.0.0.1");
        BindPolicy.check(host, System.getenv("STUDIO_TOKEN"));
        SpringApplication.run(StudioApplication.class, args);
    }
}
