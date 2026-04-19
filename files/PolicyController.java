// PolicyController.java — the REST endpoint FastAPI calls
package com.expense.controller;

import com.expense.model.ExpenseClaim;
import com.expense.model.Violation;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.kie.api.KieServices;
import org.kie.api.runtime.KieContainer;
import org.kie.api.runtime.KieSession;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/api/policy")
public class PolicyController {

    @Autowired
    private KieContainer kieContainer;

    @PostMapping("/evaluate")
    public ResponseEntity<Map<String, Object>> evaluate(@RequestBody ExpenseClaim claim) {

        // 1. Run Drools session
        KieSession session = kieContainer.newKieSession();
        List<ExpenseClaim> results = new ArrayList<>();
        session.setGlobal("results", results);
        session.insert(claim);
        session.fireAllRules();
        session.dispose();

        // 2. Compute policy_score: start at 100, deduct per violation
        int policyScore = 100;
        for (Violation v : claim.getViolations()) {
            policyScore -= v.getDeduction();
        }
        policyScore = Math.max(0, policyScore);

        // 3. Build rule_hits list for full auditability
        List<Map<String, Object>> ruleHits = new ArrayList<>();
        for (Violation v : claim.getViolations()) {
            Map<String, Object> hit = new LinkedHashMap<>();
            hit.put("rule_id", v.getRuleId());
            hit.put("severity", v.getSeverity());
            hit.put("deduction", v.getDeduction());
            hit.put("reason", v.getReason());
            ruleHits.add(hit);
        }

        // 4. Build final JSON response — this is what the decision layer receives
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("claim_id", claim.getClaimId());
        response.put("policy_engine_status", claim.getStatus());      // APPROVED / FLAGGED / REJECTED
        response.put("policy_decision", claim.getPolicyDecision());   // PASS / SOFT_FAIL / HARD_FAIL
        response.put("hard_reject", claim.isHardReject());
        response.put("violations_count", claim.getViolations().size());
        response.put("policy_score", policyScore);                    // 0–100 numeric
        response.put("rule_hits", ruleHits);
        response.put("explanations", claim.getViolations()
                .stream().map(Violation::getReason).toList());

        return ResponseEntity.ok(response);
    }
}
