// ExpenseClaim.java — the object Drools fires rules against
package com.expense.model;

import java.util.ArrayList;
import java.util.List;

public class ExpenseClaim {
    // Core fields (populated from OCR JSON by FastAPI → Spring Boot)
    private String claimId;
    private String employeeId;
    private String department;
    private String expenseCategory;
    private double amount;
    private String currency;
    private String submittedDate;
    private String vendor;
    private String justificationText;

    // Boolean flags
    private boolean receiptAttached;
    private boolean preApprovalAttached;
    private boolean isPerDiem;
    private boolean isBusinessTrip;
    private boolean isCommute;
    private boolean isWeekendSubmission;
    private boolean isTeamMeal;
    private boolean attendeeListAttached;
    private boolean mileageDocumented;
    private boolean isDuplicateFlagged;
    private boolean isInternational;

    // Transport-specific
    private String fareClass;        // ECONOMY, BUSINESS, FIRST
    private String rentalCarClass;   // ECONOMY, COMPACT, INTERMEDIATE, LUXURY
    private double ratePerKm;

    // Set by rules
    private String status;
    private String policyDecision;
    private boolean hardReject = false;
    private List<Violation> violations = new ArrayList<>();

    public void addViolation(Violation v) {
        this.violations.add(v);
    }

    // Lombok @Data would replace all these — keeping explicit for clarity
    public String getClaimId() { return claimId; }
    public String getEmployeeId() { return employeeId; }
    public String getDepartment() { return department; }
    public String getExpenseCategory() { return expenseCategory; }
    public double getAmount() { return amount; }
    public double getRatePerKm() { return ratePerKm; }
    public String getFareClass() { return fareClass; }
    public String getRentalCarClass() { return rentalCarClass; }
    public String getJustificationText() { return justificationText; }
    public boolean isReceiptAttached() { return receiptAttached; }
    public boolean isPreApprovalAttached() { return preApprovalAttached; }
    public boolean isPerDiem() { return isPerDiem; }
    public boolean isBusinessTrip() { return isBusinessTrip; }
    public boolean isCommute() { return isCommute; }
    public boolean isWeekendSubmission() { return isWeekendSubmission; }
    public boolean isTeamMeal() { return isTeamMeal; }
    public boolean isAttendeeListAttached() { return attendeeListAttached; }
    public boolean isMileageDocumented() { return mileageDocumented; }
    public boolean isDuplicateFlagged() { return isDuplicateFlagged; }
    public boolean isInternational() { return isInternational; }
    public boolean isHardReject() { return hardReject; }
    public List<Violation> getViolations() { return violations; }
    public String getStatus() { return status; }
    public String getPolicyDecision() { return policyDecision; }
    public void setStatus(String s) { this.status = s; }
    public void setPolicyDecision(String p) { this.policyDecision = p; }
    public void setHardReject(boolean h) { this.hardReject = h; }

    // Setters for deserialization
    public void setClaimId(String v) { this.claimId = v; }
    public void setEmployeeId(String v) { this.employeeId = v; }
    public void setDepartment(String v) { this.department = v; }
    public void setExpenseCategory(String v) { this.expenseCategory = v; }
    public void setAmount(double v) { this.amount = v; }
    public void setCurrency(String v) { this.currency = v; }
    public void setSubmittedDate(String v) { this.submittedDate = v; }
    public void setVendor(String v) { this.vendor = v; }
    public void setJustificationText(String v) { this.justificationText = v; }
    public void setReceiptAttached(boolean v) { this.receiptAttached = v; }
    public void setPreApprovalAttached(boolean v) { this.preApprovalAttached = v; }
    public void setPerDiem(boolean v) { this.isPerDiem = v; }
    public void setBusinessTrip(boolean v) { this.isBusinessTrip = v; }
    public void setCommute(boolean v) { this.isCommute = v; }
    public void setWeekendSubmission(boolean v) { this.isWeekendSubmission = v; }
    public void setTeamMeal(boolean v) { this.isTeamMeal = v; }
    public void setAttendeeListAttached(boolean v) { this.attendeeListAttached = v; }
    public void setMileageDocumented(boolean v) { this.mileageDocumented = v; }
    public void setDuplicateFlagged(boolean v) { this.isDuplicateFlagged = v; }
    public void setInternational(boolean v) { this.isInternational = v; }
    public void setFareClass(String v) { this.fareClass = v; }
    public void setRentalCarClass(String v) { this.rentalCarClass = v; }
    public void setRatePerKm(double v) { this.ratePerKm = v; }
}
