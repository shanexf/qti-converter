"""
Minimal paywall — no user accounts needed.

Flow:
  1. Teacher uses the tool for free up to FREE_LIMIT exports (tracked via a
     signed cookie, see main.py).
  2. When they hit the limit, the frontend calls /api/create-checkout-session
     to get a Stripe Checkout URL.
  3. After payment, Stripe calls /api/stripe-webhook, which generates a
     license key and (for now) prints/stores it — you email it to the buyer,
     or better, show it on the Stripe success page redirect URL (see README).
  4. The teacher enters the license key in the app; it's sent as a header on
     every request and checked against licenses.json.

This uses a flat JSON file as a "database" so you can ship today. Swap in a
real database (Postgres on Railway is one click) once you have paying users
— see README "Scaling beyond the MVP".
"""
import json
import os
import uuid
import stripe

LICENSE_FILE = os.path.join(os.path.dirname(__file__), "licenses.json")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")  # a recurring Stripe Price
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")


def _load_licenses():
    if not os.path.exists(LICENSE_FILE):
        return {}
    with open(LICENSE_FILE) as f:
        return json.load(f)


def _save_licenses(data):
    with open(LICENSE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_license_valid(license_key: str) -> bool:
    if not license_key:
        return False
    licenses = _load_licenses()
    entry = licenses.get(license_key)
    return bool(entry and entry.get("active"))


def create_checkout_session(customer_email: str = None) -> str:
    if not stripe.api_key or not PRICE_ID:
        raise RuntimeError(
            "Stripe isn't configured yet. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID."
        )
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        success_url=f"{APP_URL}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_URL}/?checkout=cancelled",
        customer_email=customer_email,
    )
    return session.url


def handle_webhook_event(payload: bytes, sig_header: str):
    event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        license_key = str(uuid.uuid4())
        licenses = _load_licenses()
        licenses[license_key] = {
            "active": True,
            "email": session.get("customer_email"),
            "stripe_customer": session.get("customer"),
        }
        _save_licenses(licenses)
        # In production: email this key to the customer (e.g. via a transactional
        # email API) or show it on your success page by looking it up via
        # session_id. See README for a simple approach.

    elif event["type"] in (
        "customer.subscription.deleted",
        "customer.subscription.updated",
    ):
        sub = event["data"]["object"]
        if sub.get("status") not in ("active", "trialing"):
            licenses = _load_licenses()
            for key, entry in licenses.items():
                if entry.get("stripe_customer") == sub.get("customer"):
                    entry["active"] = False
            _save_licenses(licenses)

    return event
