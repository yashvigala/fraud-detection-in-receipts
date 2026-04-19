// DroolsConfig.java — exposes a KieContainer bean so PolicyController can inject it.
//
// Drools looks for src/main/resources/META-INF/kmodule.xml at startup and
// compiles every .drl file under src/main/resources/rules/ into a KieBase.
// We simply wrap the default KieContainer as a Spring bean.
package com.expense.config;

import org.kie.api.KieServices;
import org.kie.api.runtime.KieContainer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class DroolsConfig {

    @Bean
    public KieContainer kieContainer() {
        KieServices ks = KieServices.Factory.get();
        // getKieClasspathContainer() reads META-INF/kmodule.xml and compiles
        // every rule file listed there. One container survives the lifetime
        // of the Spring app; each evaluation request gets its own KieSession.
        return ks.getKieClasspathContainer();
    }
}
