"""
One-time Stripe Checkout for fixed credit packs (not a subscription).
Each pack needs its own Stripe Price (create these in the Stripe dashboard,
see README) and its Price ID set as an env var on Railway.
"""
import os
import stripe
import db

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")

CREDIT_PACKS = {
    "50": {"credits": 50, "price_env": "STRIPE_PRICE_50", "label": "50 questions"},
    "100": {"credits": 100, "price_env": "STRIPE_PRICE_100", "label": "100 questions"},
    "200": {"credits": 200, "price_env": "STRIPE_PRICE_200", "label": "200 questions"},
}


def create_checkout_session(user_id: int, pack: str) -> str:
    pack_info = CREDIT_PACKS.get(pack)
    if not pack_info:
        raise ValueError(f"Unknown pack '{pack}'. Valid packs: {list(CREDIT_PACKS)}")

    price_id = os.environ.get(pack_info["price_env"], "")
    if not stripe.api_key or not price_id:
        raise RuntimeError(
            f"Stripe isn't fully configured yet — missing STRIPE_SECRET_KEY or "
            f"{pack_info['price_env']}."
        )

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(user_id),
        metadata={"user_id": str(user_id), "pack": pack, "credits": str(pack_info["credits"])},
        success_url=f"{APP_URL}/?checkout=success",
        cancel_url=f"{APP_URL}/?checkout=cancelled",
    )
    return session.url


def _process_checkout_completed(session: dict):
    metadata = session.get("metadata", {}) or {}
    user_id = metadata.get("user_id") or session.get("client_reference_id")
    pack = metadata.get("pack", "unknown")
    credits = int(metadata.get("credits", "0"))

    if user_id and credits > 0:
        return db.add_credits(int(user_id), credits, pack, session["id"])
    return False


def handle_webhook_event(payload: bytes, sig_header: str):
    event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)

    if event["type"] == "checkout.session.completed":
        _process_checkout_completed(event["data"]["object"])

    return event
