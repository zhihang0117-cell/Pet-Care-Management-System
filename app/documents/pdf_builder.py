"""
Two fixed PDF layouts — booking confirmation and invoice — parameterized per
company from get_billing_profile() (see company_profile.py). This is
"parameterized template" (Option A), not per-company custom design: every
company gets the identical layout/columns, only the logo/company
name/address/invoice prefix/tax name differ.
"""

from __future__ import annotations

import io
from datetime import date as _date

import requests

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_STYLES = getSampleStyleSheet()
_RIGHT_STYLE = ParagraphStyle("right", parent=_STYLES["Normal"], alignment=TA_RIGHT)
_TITLE_STYLE = ParagraphStyle("doc_title", parent=_STYLES["Title"], alignment=TA_RIGHT, fontSize=20)
_SMALL = ParagraphStyle("small", parent=_STYLES["Normal"], fontSize=8, textColor=colors.grey)


def _fetch_logo_flowable(logo_path: str | None):
    if not logo_path:
        return ""
    try:
        # requests (unlike bare urllib.request, which uses the interpreter's
        # default SSL context) verifies against certifi's CA bundle out of
        # the box — confirmed live that urllib.request.urlopen failed with
        # CERTIFICATE_VERIFY_FAILED against this exact Supabase Storage URL
        # on this machine's Python install, which silently produced a
        # logo-less PDF with no error anywhere (the broad except below
        # swallowed it). requests avoids that whole class of failure.
        resp = requests.get(logo_path, timeout=5)
        resp.raise_for_status()
        img = Image(io.BytesIO(resp.content))
        img.drawWidth = 28 * mm
        img.drawHeight = 28 * mm
        return img
    except Exception as exc:
        # A missing/unreachable logo must never block generating the
        # document itself — fall back to no logo, not a hard error. Logged
        # (not raised) so a real infra problem like the one above isn't
        # invisible forever.
        import logging

        logging.getLogger(__name__).warning("Could not fetch company logo from %s: %s", logo_path, exc)
        return ""


def _header_table(profile: dict, doc_title: str, doc_number: str, doc_date: str) -> Table:
    company_lines = [f"<b>{profile['company_name']}</b>"]
    if profile.get("address"):
        company_lines.append(profile["address"])
    company_cell = Paragraph("<br/>".join(company_lines), _STYLES["Normal"])

    title_style = ParagraphStyle(
        "doc_title_inline", parent=_RIGHT_STYLE, fontSize=14, leading=17,
        textColor=colors.HexColor("#0e6d60"), spaceAfter=4,
    )
    doc_cell = Paragraph(
        f"<b>{doc_title}</b><br/><br/>"
        f"No: {doc_number}<br/>"
        f"Date: {doc_date}",
        title_style,
    )
    logo = _fetch_logo_flowable(profile.get("logo_path"))

    table = Table(
        [[logo, company_cell, doc_cell]],
        colWidths=[28 * mm, 72 * mm, 80 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ]
        )
    )
    return table


def _rule() -> Table:
    line = Table([[""]], colWidths=[180 * mm], rowHeights=[1])
    line.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#0e6d60"))]))
    return line


def _party_table(customer_name: str, pet_name: str, staff_name: str = "", loyalty: dict | None = None) -> Table:
    data = [
        ["Customer", customer_name or "-"],
        ["Pet", pet_name or "-"],
    ]
    if staff_name:
        data.append(["Assigned Staff", staff_name])
    if loyalty:
        tier = f" ({loyalty['tier']})" if loyalty.get("tier") else ""
        data.append(["Loyalty Points", f"{loyalty.get('points_balance', 0)}{tier}"])
    table = Table(data, colWidths=[30 * mm, 150 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#54656f")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def build_booking_confirmation_pdf(
    profile: dict,
    booking: dict,
    customer_name: str,
    loyalty: dict | None = None,
    redemption: dict | None = None,
) -> bytes:
    """
    booking: {"booking_id" (real Supabase grooming_booking_id/
    daycare_booking_id/boarding_booking_id — never a made-up number),
    "service_type", "package_name", "pet_name", "staff_name",
    "date"/"check_in_date", "time"/"check_in_time", "check_out_date",
    "check_out_time", "price"/"total_price", "payment_status"}
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )
    # IDs are independent per service table, so the service must be part of
    # the human-visible reference too (GROOMING #7 and DAYCARE #7 can both
    # exist at once).
    service_type = str(booking.get("service_type") or "BOOKING").upper()
    doc_number = f"BC-{service_type}-{booking.get('booking_id')}"
    elements = [
        _header_table(
            profile, "BOOKING CONFIRMATION", doc_number,
            str(booking.get("created_date") or _date.today().isoformat()),
        ),
        Spacer(1, 10 * mm),
        _rule(),
        Spacer(1, 6 * mm),
        _party_table(customer_name, booking.get("pet_name") or "", booking.get("staff_name") or "", loyalty),
        Spacer(1, 6 * mm),
    ]

    rows = [["Service", "Package", "Date", "Time", f"Price · {profile['currency']}"]]
    date_value = booking.get("check_in_date") or booking.get("date") or booking.get("booking_date") or "-"
    time_value = booking.get("check_in_time") or booking.get("time") or booking.get("booking_time") or "-"
    base_price = booking.get("total_price") or booking.get("price") or 0
    rows.append(
        [
            service_type.title(),
            booking.get("package_name") or "-",
            str(date_value),
            str(time_value),
            str(base_price),
        ]
    )
    if service_type == "BOARDING" and booking.get("check_out_date"):
        rows.append(["", "Check-out", str(booking.get("check_out_date")), str(booking.get("check_out_time") or "-"), ""])

    # GROOMING/DAYCARE add-on, shown as its own line item (not folded into
    # the base price above) — mirrors the same add_on/add_on_price split
    # rendered in build_invoice_pdf below.
    add_on_name = booking.get("add_on")
    add_on_price = booking.get("add_on_price") or 0
    has_add_on = add_on_name and str(add_on_name).strip() not in ("", "-")
    if has_add_on:
        rows.append(["", f"Add-on: {add_on_name}", "", "", str(add_on_price)])
    price_value = float(base_price or 0) + (float(add_on_price) if has_add_on else 0)
    if redemption:
        status = str(redemption.get("status") or "Pending").title()
        points = redemption.get("points_spent") or 0
        reward = redemption.get("reward_name") or "Loyalty voucher"
        reward_type = str(redemption.get("reward_type") or "")
        if reward_type == "Free service":
            discount = price_value
        else:
            try:
                discount = min(price_value, float(redemption.get("discount_value") or 0))
            except (TypeError, ValueError):
                discount = 0
        verb = "applied" if status == "Approved" else "requested — pending staff approval"
        rows.append(["", f"{reward} ({points} points; {verb})", "", "", f"-{discount:g}"])
        price_value = max(0, price_value - discount)
    if has_add_on or redemption:
        rows.append(["", "", "", "Total", f"{price_value:g}"])

    table = Table(rows, colWidths=[28 * mm, 62 * mm, 30 * mm, 30 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e6d60")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d7db")),
                ("ALIGN", (4, 0), (4, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 8 * mm))
    # payment_status ("Pending"/"Paid"/etc), not booking_status — every new
    # booking is "Pending"/"Scheduled" at the moment this is generated
    # regardless of path, so that tells the customer nothing new; whether
    # they still need to pay is the actual open question this line answers.
    elements.append(
        Paragraph(f"<b>Payment Status:</b> {booking.get('payment_status') or 'Pending'}", _STYLES["Normal"])
    )
    elements.append(Spacer(1, 4 * mm))
    elements.append(
        Paragraph(
            f"Please quote booking reference <b>{doc_number}</b> for any cancellation, "
            "rescheduling, or enquiry.",
            _STYLES["Normal"],
        )
    )
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("This is a system-generated confirmation, no signature required.", _SMALL))

    doc.build(elements)
    return buffer.getvalue()


def build_invoice_pdf(
    profile: dict,
    payment: dict,
    booking: dict,
    customer_name: str,
    loyalty: dict | None = None,
    redemption: dict | None = None,
) -> bytes:
    """
    payment: {"payment_id", "service", "base_price", "add_ons",
    "final_amount", "payment_method", "date", "status"}
    booking: same shape as build_booking_confirmation_pdf's booking arg
    (real Supabase booking_id + staff_name), used for pet_name/booking_id
    cross-reference. redemption: {"points_spent", "reward_name"} if THIS
    payment has a linked loyalty redemption, else None (see
    app.documents.service._redemption_for_payment).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )
    doc_number = f"{profile['invoice_prefix']}-{payment.get('payment_id')}"
    elements = [
        _header_table(profile, "INVOICE", doc_number, str(payment.get("date") or "")),
        Spacer(1, 10 * mm),
        _rule(),
        Spacer(1, 6 * mm),
        _party_table(customer_name, booking.get("pet_name") or "", booking.get("staff_name") or "", loyalty),
        Spacer(1, 6 * mm),
    ]

    base_price = payment.get("base_price") or 0
    add_ons = payment.get("add_ons") or ""
    final_amount = payment.get("final_amount")
    if final_amount is None:
        final_amount = base_price

    currency = profile["currency"]
    rows = [["Description", f"Amount · {currency}"]]
    rows.append([payment.get("service") or "Service", str(base_price)])
    if add_ons and str(add_ons).strip() not in ("", "-"):
        rows.append([str(add_ons), str(booking.get("add_on_price") or "")])
    if redemption:
        reward_label = f" — {redemption['reward_name']}" if redemption.get("reward_name") else ""
        subtotal = float(base_price) + float(booking.get("add_on_price") or 0)
        discount = round(subtotal - float(final_amount), 2)
        if discount == int(discount):
            discount = int(discount)
        rows.append(
            [
                f"Loyalty redemption{reward_label} ({redemption.get('points_spent', 0)} points used)",
                f"-{discount}",
            ]
        )
    rows.append(["", ""])
    rows.append([Paragraph("<b>Total</b>", _STYLES["Normal"]), Paragraph(f"<b>{final_amount}</b>", _RIGHT_STYLE)])

    table = Table(rows, colWidths=[130 * mm, 50 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e6d60")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#0e6d60")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph(f"<b>Payment method:</b> {payment.get('payment_method') or '-'}", _STYLES["Normal"]))
    elements.append(Paragraph(f"<b>Status:</b> {payment.get('status') or '-'}", _STYLES["Normal"]))
    if booking.get("booking_id"):
        elements.append(Paragraph(f"<b>Booking reference:</b> #{booking.get('booking_id')}", _STYLES["Normal"]))
    if profile.get("tax_name"):
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(f"Prices shown are inclusive of {profile['tax_name']}, where applicable.", _SMALL))
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("This is a system-generated invoice, no signature required.", _SMALL))

    doc.build(elements)
    return buffer.getvalue()
