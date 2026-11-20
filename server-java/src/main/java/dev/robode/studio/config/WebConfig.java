package dev.robode.studio.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** CORS is off unless STUDIO_CORS_ORIGINS lists origins (the Vite dev server proxies /api, so none are needed). */
@Configuration
class WebConfig implements WebMvcConfigurer {
    private final StudioProperties properties;

    WebConfig(StudioProperties properties) { this.properties = properties; }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        if (properties.corsOrigins().isEmpty()) return;
        registry.addMapping("/api/v1/**").allowedOrigins(properties.corsOrigins().toArray(String[]::new))
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS").allowedHeaders("*");
    }
}
