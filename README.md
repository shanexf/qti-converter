# Keyform — Docx/PDF → QTI 2.2 Converter

A small SaaS: teachers upload a Word or PDF question set, review/edit the parsed
questions, and export a QTI 2.2 package for their LMS. Free tier + paid
subscription unlock unlimited exports.

## Project structure

```
qti-converter/
  backend/          FastAPI app — parsing, QTI generation, billing
    main.py
    extract.py       docx/pdf → plain text
    parser.py        plain text → structured questions
    qti_generator.py structured questions → QTI 2.2 zip
    billing.py        Stripe checkout + license verification
    requirements.txt
    Procfile
  frontend/
    index.html        the whole UI, no build step needed
```

## 1. Run it locally first

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then just open `frontend/index.html` in your browser (double-click it, or
`python -m http.server` from the `frontend/` folder). It's already pointed at
`http://localhost:8000` when running locally. Upload a `.docx` formatted per
the convention shown in the app, confirm the parsed cards look right, and
export.

## 2. Deploy the backend to Railway

1. Push this whole folder to a GitHub repo.
2. On [railway.app](https://railway.app), **New Project → Deploy from GitHub repo**.
3. When Railway asks for the root directory, set it to `backend/`.
4. Railway auto-detects Python via `requirements.txt` and uses the `Procfile`
   to start the server — no extra config needed.
5. Under **Variables**, add:
   - `COOKIE_SECRET` — any long random string (this signs the free-usage cookie)
   - `ALLOWED_ORIGINS` — your frontend's domain, e.g. `https://keyform.yourdomain.com`
   - `FREE_EXPORT_LIMIT` — optional, defaults to `3`
   - Stripe variables — see step 4 below
6. Railway gives you a URL like `https://keyform-backend.up.railway.app`.
   Test it: `https://your-url.up.railway.app/api/health` should return `{"status":"ok"}`.

## 3. Deploy the frontend and connect your domain

The frontend is a single static HTML file, so any static host works
(Netlify, Vercel, GitHub Pages, Cloudflare Pages, or Railway's static hosting).

1. Before deploying, edit `frontend/index.html`: replace
   `https://YOUR-RAILWAY-BACKEND.up.railway.app` with your real backend URL
   from step 2.
2. Deploy the `frontend/` folder to your chosen static host.
3. Point your domain's DNS at that host (each host walks you through this —
   typically a `CNAME` record). Once your registrar's DNS propagates
   (can take up to a few hours), your app is live at your own domain.

## 4. Set up Stripe (subscription payments)

1. Create a [Stripe account](https://dashboard.stripe.com/register) — EU-based
   is fine, Stripe supports SEPA payouts.
2. **Products → Add product**, e.g. "Keyform Pro", price €7/month recurring.
   Copy the **Price ID** (starts `price_...`).
3. **Developers → API keys** → copy your **Secret key**.
4. On Railway, set:
   - `STRIPE_SECRET_KEY` = your secret key
   - `STRIPE_PRICE_ID` = the price ID from step 2
   - `APP_URL` = your frontend's public URL
5. **Developers → Webhooks → Add endpoint**: point it at
   `https://your-backend-url/api/stripe-webhook`, select the
   `checkout.session.completed` and `customer.subscription.updated` /
   `.deleted` events. Copy the **signing secret** into `STRIPE_WEBHOOK_SECRET`
   on Railway.
6. Test with Stripe's [test mode](https://docs.stripe.com/testing) card
   `4242 4242 4242 4242` before going live.

**Getting the license key to the buyer:** right now the webhook generates a
key and stores it, but doesn't yet email it anywhere. The fastest fix: use a
free tier of [Resend](https://resend.com) or SendGrid to email the key in
`billing.py`'s webhook handler, or show it directly on your Stripe success
page by looking up the session — ask me and I'll wire either one up.

## 5. About ads (optional, later)

Once you have meaningful traffic, Google AdSense is the easiest network to
start with. Two things to know in advance since you're serving EU users:
- You'll need a **cookie consent banner** before any ad/tracking scripts load
  (GDPR requirement) — a free option is [Cookiebot](https://www.cookiebot.com)
  or a simple self-built banner.
- AdSense approval takes some days and generally wants a live site with real
  content and traffic first, so it's worth adding after you've validated the
  subscription works.

## 6. Scaling beyond the MVP

The license store is a flat JSON file on the backend's disk, which is fine
for the first handful of customers but **will reset if Railway redeploys the
service** (its filesystem isn't persistent across deploys). Before relying on
this for real revenue, move `billing.py`'s storage to a database — Railway
offers one-click Postgres, and I can wire that up with you when you're ready.

## Notes on the QTI output

- Targets **QTI 2.2**, packaged as a zip of `assessmentItem` XML files plus
  `imsmanifest.xml`, which most LMSs (Canvas, Moodle, Blackboard) can import
  directly.
- Multiple choice / true-false / matching are auto-scored. Fill-in-the-blank
  is scored as an **exact, case-sensitive text match** — mention this to the
  teacher, since minor spelling variation won't be marked correct.
- Essay questions are ungraded by QTI (manual grading in the LMS), as is
  standard for free-response items.
