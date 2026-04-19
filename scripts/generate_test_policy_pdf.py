"""Generate a sample company expense-policy PDF for testing the PDF->rules
import flow. Produces 5 policy rules stated in natural English, covering
different predicate types so Gemini has to actually parse and map them."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

OUT = Path(__file__).resolve().parent.parent / "data" / "test_policy.pdf"


def build() -> Path:
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        title="NovaCorp — Expense Policy",
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=22*mm, bottomMargin=22*mm,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                        fontSize=22, leading=26, textColor=colors.HexColor("#1F1E1D"),
                        spaceAfter=6)
    sub = ParagraphStyle("sub", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=10, textColor=colors.HexColor("#87827A"),
                          leading=14, spaceAfter=16)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=14, leading=18, textColor=colors.HexColor("#C15F3C"),
                        spaceBefore=18, spaceAfter=8)
    rule_id = ParagraphStyle("rule_id", parent=styles["BodyText"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=colors.HexColor("#1F1E1D"),
                             spaceAfter=2)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=11, leading=16, textColor=colors.HexColor("#1F1E1D"),
                          spaceAfter=8)
    muted = ParagraphStyle("muted", parent=body, fontSize=9,
                           textColor=colors.HexColor("#87827A"))

    story = []

    story.append(Paragraph("NovaCorp — Expense Policy", h1))
    story.append(Paragraph(
        "Effective 1 April 2026 · Applies to all employees submitting expense claims. "
        "Violations fall under HARD (auto-rejected) or SOFT (manager review required).",
        sub,
    ))

    story.append(Paragraph("1. Lodging", h2))
    story.append(Paragraph("<b>Rule LODGING_CAP</b> — HARD", rule_id))
    story.append(Paragraph(
        "Hotel and accommodation expenses are capped at ₹5,000 per night within "
        "India. Any lodging claim (category: Lodging) with an amount greater than "
        "₹5,000 is automatically rejected. The reason surfaced to the employee "
        "should be: <i>Lodging claim of {amount} exceeds the ₹5,000/night cap.</i>",
        body,
    ))

    story.append(Paragraph("2. Meals", h2))
    story.append(Paragraph("<b>Rule MEAL_GUIDELINE</b> — SOFT", rule_id))
    story.append(Paragraph(
        "Individual meal claims (category: Food) should not exceed ₹1,200 per meal. "
        "Claims above this threshold are flagged for manager review with the "
        "message: <i>Meal {amount} exceeds the ₹1,200 per-meal guideline.</i> "
        "This is a soft limit — approval is at the manager's discretion.",
        body,
    ))

    story.append(Paragraph("3. Department restrictions", h2))
    story.append(Paragraph("<b>Rule ENGINEERING_ENTERTAINMENT</b> — HARD", rule_id))
    story.append(Paragraph(
        "Employees in the Engineering department are not permitted to claim "
        "Entertainment expenses. Any claim with category: Entertainment and "
        "department: Engineering is hard-rejected. Message shown to the employee: "
        "<i>Engineering employees cannot claim Entertainment expenses — please "
        "speak to your manager.</i>",
        body,
    ))

    story.append(Paragraph("4. Per diem", h2))
    story.append(Paragraph("<b>Rule PER_DIEM_CAP</b> — HARD", rule_id))
    story.append(Paragraph(
        "Daily per-diem allowance is capped at ₹2,500. Claims flagged as per-diem "
        "(is_per_diem: true) with amount greater than ₹2,500 are hard-rejected "
        "with the message: <i>Per-diem claim of {amount} exceeds the ₹2,500 daily "
        "maximum.</i>",
        body,
    ))

    story.append(Paragraph("5. Weekend submissions", h2))
    story.append(Paragraph("<b>Rule WEEKEND_CHECK</b> — SOFT", rule_id))
    story.append(Paragraph(
        "Expense claims submitted on a Saturday or Sunday (is_weekend: true) when "
        "the employee is not on a business trip (is_business_trip: false) are "
        "flagged for manager review. The rule fires with deduction 20 and the "
        "message: <i>Weekend submission without an active business trip — "
        "manager approval required.</i>",
        body,
    ))

    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "This policy supersedes all previous expense guidelines. For questions "
        "contact finance@novacorp.demo. "
        "Rules are enforced automatically via the ExpenseAI policy engine.",
        muted,
    ))

    doc.build(story)
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"Wrote {p} ({p.stat().st_size / 1024:.1f} KB)")
