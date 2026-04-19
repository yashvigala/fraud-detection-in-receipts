// Violation.java — one rule hit
package com.expense.model;

public class Violation {
    private String ruleId;
    private String severity;   // "HARD" or "SOFT"
    private int deduction;     // 0-100 penalty score
    private String reason;

    public Violation(String ruleId, String severity, int deduction, String reason) {
        this.ruleId = ruleId;
        this.severity = severity;
        this.deduction = deduction;
        this.reason = reason;
    }
    // getters/setters omitted for brevity — generate with Lombok @Data
    public String getRuleId() { return ruleId; }
    public String getSeverity() { return severity; }
    public int getDeduction() { return deduction; }
    public String getReason() { return reason; }
}
