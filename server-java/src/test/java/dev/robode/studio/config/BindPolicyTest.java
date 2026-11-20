package dev.robode.studio.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class BindPolicyTest {
    @Test
    void loopbackNeedsNoToken() {
        for (String host : new String[] {"127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.5"})
            assertThatCode(() -> BindPolicy.check(host, null)).as(host).doesNotThrowAnyException();
    }

    @Test
    void nonLoopbackWithoutTokenIsRefused() {
        for (String host : new String[] {"0.0.0.0", "192.168.1.20", "::", "example.com", ""})
            assertThatThrownBy(() -> BindPolicy.check(host, null)).as(host).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> BindPolicy.check("0.0.0.0", "  ")).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void nonLoopbackWithTokenIsAllowed() {
        assertThatCode(() -> BindPolicy.check("0.0.0.0", "secret")).doesNotThrowAnyException();
        assertThat(BindPolicy.isLoopback("localhost")).isTrue();
    }
}
