from langchain_core.tools import tool

from app.db.customer_context import CustomerContext
from app.db.relational_provider import get_relational_repository


@tool
def get_loyalty_balance(company_id: str | int, customer_id: str | int) -> dict:
    """Get the customer's current loyalty points balance and tier."""
    return get_relational_repository().get_loyalty_account(int(company_id), int(customer_id))


@tool
def check_coupon_eligibility(company_id: str | int, customer_id: str | int) -> dict:
    """List redeemable loyalty coupons and whether the customer currently has enough points for each."""
    from app.db.relational_actions import check_coupon_eligibility as _check_coupons

    context = CustomerContext(company_id=int(company_id))
    context.resolved_customer_id = int(customer_id)
    return _check_coupons(context)


@tool
def register_loyalty_member(company_id: str | int, customer_id: str | int, confirmed: bool = False) -> dict:
    """
    Enrol a customer in the loyalty program (starts at 0 points, Bronze
    tier). Call with confirmed=False (or omitted) FIRST to preview — it
    returns confirmation_required without writing anything. Only call again
    with confirmed=True after the customer has actually said yes to your
    offer; never enrol someone silently. If they're already a member, this
    just returns their existing account (safe to call either way).
    """
    from app.db.relational_actions import register_loyalty_member as _register

    context = CustomerContext(company_id=int(company_id))
    context.resolved_customer_id = int(customer_id)
    return _register(context, confirmed=confirmed)


@tool
def redeem_reward(company_id: str | int, customer_id: str | int, payment_id: str | int, coupon_id: str | int) -> dict:
    """
    Submit a loyalty coupon redemption REQUEST against a specific unpaid
    payment. Call this ONLY after create_booking has already succeeded —
    payment_id must be the real payment_id from create_booking's result
    (never a booking_id, and never guessed), and coupon_id must be a real
    coupon_id from check_coupon_eligibility's eligible_coupons.

    This does NOT deduct any points and does NOT apply a discount
    immediately — a staff member must approve the request from the
    dashboard first; only then are points actually deducted and the
    discount applied to the payment. Tell the customer their request has
    been submitted for approval, not that points were spent or a discount
    is already confirmed.
    """
    intent_json = {"entities": {"payment_id": str(payment_id), "coupon_id": str(coupon_id)}}
    return get_relational_repository().redeem_reward(int(company_id), int(customer_id), intent_json)
