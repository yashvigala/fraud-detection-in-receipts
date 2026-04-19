"""Generate additional company-policy PDFs for testing the PDF → rules
import flow. Each PDF has a different "company personality" and hits a
different mix of DSL predicates.

Outputs (all under data/):
    test_policy.pdf          — NovaCorp (original — basic rules)
    test_policy_apex.pdf     — Apex Financial (strict bank, international travel)
    test_policy_pixel.pdf    — Pixel Studios (creative agency, fraud patterns)
    test_policy_buildco.pdf  — BuildCo Logistics (manufacturing, operational)
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

OUT_DIR = Path(__file__).resolve().parent.parent / "data"


# ----- shared styling -----
def _styles():
    s = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "h1", parent=s["Heading1"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, textColor=colors.HexColor("#1F1E1D"),
            spaceAfter=6,
        ),
        "sub": ParagraphStyle(
            "sub", parent=s["BodyText"], fontName="Helvetica",
            fontSize=10, textColor=colors.HexColor("#87827A"),
            leading=14, spaceAfter=16,
        ),
        "h2": ParagraphStyle(
            "h2", parent=s["Heading2"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, textColor=colors.HexColor("#C15F3C"),
            spaceBefore=18, spaceAfter=8,
        ),
        "ruleid": ParagraphStyle(
            "ruleid", parent=s["BodyText"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=colors.HexColor("#1F1E1D"),
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=s["BodyText"], fontName="Helvetica",
            fontSize=11, leading=16, textColor=colors.HexColor("#1F1E1D"),
            spaceAfter=8,
        ),
        "muted": ParagraphStyle(
            "muted", parent=s["BodyText"], fontName="Helvetica",
            fontSize=9, leading=13, textColor=colors.HexColor("#87827A"),
        ),
    }


def _build(path: Path, title: str, subtitle: str, sections: list[tuple[str, list[tuple[str, str, str]]]]) -> Path:
    """Render a policy PDF.

    ``sections`` is a list of (section_heading, list_of_rules) pairs.
    Each rule is a (rule_id_and_severity, name_paragraph, body_paragraph) triple.
    """
    st = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, title=title,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=22*mm, bottomMargin=22*mm,
    )
    story = [Paragraph(title, st["h1"]), Paragraph(subtitle, st["sub"])]
    for heading, rules in sections:
        story.append(Paragraph(heading, st["h2"]))
        for header_line, _name, body_txt in rules:
            story.append(Paragraph(header_line, st["ruleid"]))
            story.append(Paragraph(body_txt, st["body"]))
    story.append(Spacer(1, 28))
    story.append(Paragraph(
        "This policy supersedes previous guidelines. Rules are enforced automatically "
        "via the ExpenseAI policy engine.",
        st["muted"],
    ))
    doc.build(story)
    return path


# =============================================================================
# PDF #2 — APEX FINANCIAL (strict bank)
# =============================================================================
def build_apex() -> Path:
    return _build(
        OUT_DIR / "test_policy_apex.pdf",
        title="Apex Financial Services — Expense Policy",
        subtitle=(
            "Effective 1 Jan 2026 · Conservative financial-services policy. "
            "HARD violations are auto-rejected. SOFT violations require manager review."
        ),
        sections=[
            ("1. Non-reimbursable categories", [
                ("Rule ENT_NONREIMB — HARD", "",
                 "Entertainment expenses are categorically non-reimbursable at Apex Financial. "
                 "Any claim with category: Entertainment is auto-rejected regardless of amount. "
                 "Message: <i>Entertainment expenses are not reimbursable per Apex policy.</i>"),
            ]),
            ("2. International travel", [
                ("Rule INTL_TRAVEL_CAP — HARD", "",
                 "International travel claims (is_international: true) exceeding ₹75,000 "
                 "require Director-level pre-approval. When the amount is greater than ₹75,000 "
                 "and pre_approval_attached is false, the claim is rejected with message: "
                 "<i>International travel of {amount} requires Director pre-approval.</i>"),
            ]),
            ("3. Fraud indicators", [
                ("Rule ROUND_AMOUNT_FLAG — SOFT", "",
                 "Amounts that are round multiples of 1,000 and above ₹10,000 are flagged "
                 "(amount_is_round_multiple_of: 1000, amount_gt: 10000). Manager review "
                 "with deduction 20. Message: <i>Round amount {amount} — manager review recommended.</i>"),
            ]),
            ("4. Team meals", [
                ("Rule TEAM_MEAL_LIST — SOFT", "",
                 "Team meals (is_team_meal: true) without attendee_list_attached are flagged "
                 "for review. Message: <i>Team meal of {amount} missing attendee list — "
                 "please attach and resubmit.</i>"),
            ]),
            ("5. Justification for large claims", [
                ("Rule LARGE_NO_JUSTIFICATION — SOFT", "",
                 "Any claim with amount greater than ₹5,000 where justification_missing "
                 "is true flags for review. Message: <i>Claim of {amount} requires written "
                 "justification — please add context and resubmit.</i>"),
            ]),
        ],
    )


# =============================================================================
# PDF #3 — PIXEL STUDIOS (creative agency, fraud-pattern focused)
# =============================================================================
def build_pixel() -> Path:
    return _build(
        OUT_DIR / "test_policy_pixel.pdf",
        title="Pixel Studios — Creative Team Expense Policy",
        subtitle=(
            "Effective 15 March 2026 · Lenient reimbursement policy with strong "
            "fraud pattern detection. HARD = rejected, SOFT = flagged for producer review."
        ),
        sections=[
            ("1. Non-reimbursable vendors", [
                ("Rule GROCERY_VENDOR_BLOCK — HARD", "",
                 "Claims from grocery vendors are not reimbursable. If vendor_contains the "
                 "text 'grocery', the claim is rejected. Message: <i>Groceries from {vendor} "
                 "are personal expenses and not reimbursable.</i>"),
                ("Rule LOUNGE_VENDOR_BLOCK — HARD", "",
                 "Airport and hotel lounges are not reimbursed. If vendor_contains 'lounge', "
                 "the claim is rejected. Message: <i>Lounge access ({vendor}) is a personal "
                 "expense.</i>"),
            ]),
            ("2. Late-night submission patterns", [
                ("Rule LATE_NIGHT_FLAG — SOFT", "",
                 "Submissions where hour_gt: 21 (after 21:00) are flagged for producer review. "
                 "Deduction 15. Message: <i>Submitted at an unusual hour — flagged for review.</i>"),
            ]),
            ("3. Weekend submission rule", [
                ("Rule WEEKEND_NONBUSINESS — SOFT", "",
                 "Claims submitted on weekends (is_weekend: true) when the employee is not "
                 "on a business trip (is_business_trip: false) are flagged. Message: "
                 "<i>Weekend submission with no active trip — producer approval required.</i>"),
            ]),
            ("4. Grade-based limits", [
                ("Rule JUNIOR_DAILY_LIMIT — HARD", "",
                 "Junior-grade employees (grade: Junior) cannot submit single claims above "
                 "₹4,000 (amount_gt: 4000). Claims above this are rejected. Message: "
                 "<i>Junior-grade claims are limited to ₹4,000 per submission.</i>"),
            ]),
        ],
    )


# =============================================================================
# PDF #4 — BUILDCO LOGISTICS (manufacturing/operations)
# =============================================================================
def build_buildco() -> Path:
    return _build(
        OUT_DIR / "test_policy_buildco.pdf",
        title="BuildCo Logistics — Operational Expense Policy",
        subtitle=(
            "Effective 1 February 2026 · Strict operational controls. Engineering "
            "department operates under stricter limits. HARD rules are auto-rejected."
        ),
        sections=[
            ("1. Fuel reimbursement", [
                ("Rule FUEL_DEPT_CHECK — HARD", "",
                 "Fuel expenses (category: Fuel) may only be claimed by the Operations "
                 "department. If department_in is any of [Sales, Marketing, HR, Finance], "
                 "the fuel claim is rejected. Message: <i>Fuel expenses are restricted to "
                 "the Operations department at BuildCo.</i>"),
            ]),
            ("2. Heavy equipment / procurement", [
                ("Rule HEAVY_EQUIPMENT_PREAPPROVAL — HARD", "",
                 "Office Supplies claims above ₹8,000 require pre-approval. When "
                 "category: Office Supplies, amount_gt: 8000, and pre_approval_attached "
                 "is false, the claim is rejected with message: <i>Office Supplies above "
                 "₹8,000 require pre-approval from the procurement team.</i>"),
            ]),
            ("3. Approved vendor list", [
                ("Rule UNAPPROVED_ONLINE_VENDOR — HARD", "",
                 "Online marketplace purchases are not allowed for Office Supplies — all "
                 "procurement must go through corporate suppliers. If category: Office Supplies "
                 "and vendor_contains 'amazon', the claim is rejected. Message: <i>BuildCo "
                 "procurement must use corporate suppliers. Amazon purchases are not allowed.</i>"),
            ]),
            ("4. Junior-grade lodging", [
                ("Rule LODGING_JUNIOR_CAP — SOFT", "",
                 "Junior employees (grade: Junior) with Lodging (category: Lodging) claims "
                 "above ₹2,500 are flagged for review. Deduction 25. Message: "
                 "<i>Junior lodging {amount} above ₹2,500/night — please justify.</i>"),
            ]),
            ("5. Receipt requirement", [
                ("Rule RECEIPT_MANDATORY — HARD", "",
                 "All non-per-diem claims (is_per_diem: false) must have receipts attached. "
                 "If receipt_attached is false, the claim is rejected. Message: "
                 "<i>Receipt is mandatory for all non-per-diem claims at BuildCo.</i>"),
            ]),
        ],
    )


if __name__ == "__main__":
    paths = [build_apex(), build_pixel(), build_buildco()]
    print("Generated:")
    for p in paths:
        print(f"  {p}  ({p.stat().st_size/1024:.1f} KB)")
