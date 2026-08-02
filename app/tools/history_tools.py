from langchain_core.tools import tool

from app.db.customer_context import CustomerContext


@tool
def get_payment_history(company_id: str | int, customer_id: str | int) -> dict:
    """
    Get the customer's past payments (service, amount, method, date, status)
    across all their pets' bookings. Call this when the customer asks what
    they paid, for a receipt/history, or about a specific past charge.
    Returns at most the 10 most recent payments.
    """
    from app.db.relational_actions import get_payment_history as _get_payment_history

    context = CustomerContext(company_id=int(company_id))
    context.resolved_customer_id = int(customer_id)
    return _get_payment_history(context)


@tool
def get_redemption_history(company_id: str | int, customer_id: str | int) -> dict:
    """
    Get the customer's past loyalty reward redemptions (which coupon, points
    spent/earned, date, status). Call this when the customer asks what
    they've redeemed before or wants their redemption history — this is
    different from check_coupon_eligibility (which is about what they CAN
    redeem now). Returns at most the 10 most recent redemptions.
    """
    from app.db.relational_actions import get_redemption_history as _get_redemption_history

    context = CustomerContext(company_id=int(company_id))
    context.resolved_customer_id = int(customer_id)
    return _get_redemption_history(context)
