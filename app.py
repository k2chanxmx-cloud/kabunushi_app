from flask import Flask, render_template, request, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import re
import json
from dotenv import load_dotenv
from google.cloud import vision
from google.oauth2 import service_account
from pywebpush import webpush

load_dotenv()

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

GOOGLE_VISION_CREDENTIALS_JSON = os.environ.get(
    "GOOGLE_VISION_CREDENTIALS_JSON"
)

VAPID_PUBLIC_KEY = os.environ.get(
    "VAPID_PUBLIC_KEY"
)

VAPID_PRIVATE_KEY = os.environ.get(
    "VAPID_PRIVATE_KEY"
)

VAPID_SUBJECT = os.environ.get(
    "VAPID_SUBJECT"
)


# =========================================
# DB
# =========================================

def get_conn():
    return psycopg2.connect(DATABASE_URL)


# =========================================
# INIT DB
# =========================================

def init_db():

    with get_conn() as conn:

        with conn.cursor() as cur:

            # tickets

            cur.execute("""

                CREATE TABLE IF NOT EXISTS tickets (

                    id SERIAL PRIMARY KEY,

                    company TEXT NOT NULL,

                    balance INTEGER NOT NULL DEFAULT 0,

                    expire_date DATE,

                    category TEXT NOT NULL DEFAULT 'その他',

                    memo TEXT,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

            """)

            cur.execute("""
                ALTER TABLE tickets
                ADD COLUMN IF NOT EXISTS memo TEXT;
            """)

            # logs

            cur.execute("""

                CREATE TABLE IF NOT EXISTS logs (

                    id SERIAL PRIMARY KEY,

                    message TEXT,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

            """)

            # push subscriptions

            cur.execute("""

                CREATE TABLE IF NOT EXISTS push_subscriptions (

                    id SERIAL PRIMARY KEY,

                    subscription JSONB NOT NULL,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

            """)

        conn.commit()


@app.before_request
def before_request():
    init_db()


# =========================================
# GOOGLE VISION
# =========================================

def get_vision_client():

    info = json.loads(
        GOOGLE_VISION_CREDENTIALS_JSON
    )

    credentials = service_account.Credentials.from_service_account_info(
        info
    )

    return vision.ImageAnnotatorClient(
        credentials=credentials
    )


# =========================================
# OCR PARSE
# =========================================

def extract_amount_candidates(text):

    candidates = []

    patterns = [

        r"([0-9]{1,3}(?:,[0-9]{3})+)\s*円",
        r"([0-9]+)\s*円",

        r"([0-9]{1,3}(?:,[0-9]{3})+)\s*pt",
        r"([0-9]+)\s*pt",

        r"([0-9]{1,3}(?:,[0-9]{3})+)\s*P",
        r"([0-9]+)\s*P",

    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE
        ):

            raw = match.group(1)

            value = int(
                raw.replace(",", "")
            )

            if value not in [
                c["value"]
                for c in candidates
            ]:

                candidates.append({

                    "value": value,

                    "display": f"{value:,}",

                    "raw": match.group(0)

                })

    return candidates


# =========================================
# PUSH
# =========================================

def send_push_notification(
    title,
    body
):

    with get_conn() as conn:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""

                SELECT subscription
                FROM push_subscriptions;

            """)

            rows = cur.fetchall()

    for row in rows:

        subscription = row["subscription"]

        try:

            webpush(

                subscription_info=subscription,

                data=json.dumps({

                    "title": title,
                    "body": body

                }),

                vapid_private_key=VAPID_PRIVATE_KEY,

                vapid_claims={
                    "sub": VAPID_SUBJECT
                }

            )

        except Exception as e:

            print(e)


# =========================================
# HOME
# =========================================

@app.route("/")
def home():

    with get_conn() as conn:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""

                SELECT
                    id,
                    company,
                    balance,
                    expire_date,
                    category,
                    memo,
                    created_at

                FROM tickets

                ORDER BY
                    expire_date IS NULL,
                    expire_date ASC,
                    id DESC;

            """)

            tickets = cur.fetchall()

            cur.execute("""

                SELECT
                    id,
                    message,
                    created_at

                FROM logs

                ORDER BY id DESC

                LIMIT 20;

            """)

            logs = cur.fetchall()

    total_balance = sum(
        ticket["balance"]
        for ticket in tickets
    )

    ticket_count = len(tickets)

    return render_template(

        "index.html",

        tickets=tickets,

        total_balance=total_balance,

        ticket_count=ticket_count,

        logs=logs,

        ocr_text=None,

        ocr_candidates=[],

        vapid_public_key=VAPID_PUBLIC_KEY

    )


# =========================================
# ADD
# =========================================

@app.route("/add", methods=["POST"])
def add_ticket():

    company = request.form.get(
        "company",
        ""
    ).strip()

    balance = request.form.get(
        "balance",
        "0"
    ).strip()

    expire_date = request.form.get(
        "expire_date",
        ""
    ).strip()

    category = request.form.get(
        "category",
        "その他"
    ).strip()

    memo = request.form.get(
        "memo",
        ""
    ).strip()

    if not company:
        return redirect(url_for("home"))

    try:
        balance = int(balance)

    except ValueError:
        balance = 0

    if expire_date == "":
        expire_date = None

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""

                INSERT INTO tickets
                    (
                        company,
                        balance,
                        expire_date,
                        category,
                        memo
                    )

                VALUES
                    (%s, %s, %s, %s, %s);

            """, (

                company,
                balance,
                expire_date,
                category,
                memo

            ))

            cur.execute("""

                INSERT INTO logs
                    (message)

                VALUES
                    (%s);

            """, (

                f"{company} を登録しました",

            ))

        conn.commit()

    return redirect(url_for("home"))


# =========================================
# DELETE
# =========================================

@app.route("/delete/<int:ticket_id>")
def delete_ticket(ticket_id):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""

                SELECT company
                FROM tickets
                WHERE id = %s;

            """, (ticket_id,))

            row = cur.fetchone()

            company_name = (
                row[0]
                if row
                else "優待"
            )

            cur.execute("""

                DELETE FROM tickets
                WHERE id = %s;

            """, (ticket_id,))

            cur.execute("""

                INSERT INTO logs
                    (message)

                VALUES
                    (%s);

            """, (

                f"{company_name} を削除しました",

            ))

        conn.commit()

    return redirect(url_for("home"))


# =========================================
# OCR
# =========================================

@app.route("/ocr", methods=["POST"])
def ocr_upload():

    file = request.files.get(
        "screenshot"
    )

    if not file:
        return redirect(url_for("home"))

    image_bytes = file.read()

    client = get_vision_client()

    image = vision.Image(
        content=image_bytes
    )

    response = client.text_detection(
        image=image
    )

    texts = response.text_annotations

    ocr_text = (
        texts[0].description
        if texts
        else ""
    )

    candidates = extract_amount_candidates(
        ocr_text
    )

    with get_conn() as conn:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""

                SELECT
                    id,
                    company,
                    balance,
                    expire_date,
                    category,
                    memo,
                    created_at

                FROM tickets

                ORDER BY
                    expire_date IS NULL,
                    expire_date ASC,
                    id DESC;

            """)

            tickets = cur.fetchall()

            cur.execute("""

                SELECT
                    id,
                    message,
                    created_at

                FROM logs

                ORDER BY id DESC

                LIMIT 20;

            """)

            logs = cur.fetchall()

    total_balance = sum(
        ticket["balance"]
        for ticket in tickets
    )

    ticket_count = len(tickets)

    return render_template(

        "index.html",

        tickets=tickets,

        total_balance=total_balance,

        ticket_count=ticket_count,

        logs=logs,

        ocr_text=ocr_text,

        ocr_candidates=candidates,

        vapid_public_key=VAPID_PUBLIC_KEY

    )


# =========================================
# SAVE PUSH
# =========================================

@app.route(
    "/save-subscription",
    methods=["POST"]
)
def save_subscription():

    data = request.get_json()

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""

                INSERT INTO push_subscriptions
                    (subscription)

                VALUES
                    (%s);

            """, (

                json.dumps(data),

            ))

        conn.commit()

    return {
        "ok": True
    }


# =========================================
# TEST PUSH
# =========================================

@app.route("/test-notification")
def test_notification():

    send_push_notification(

        "株主優待管理",

        "通知テストです"

    )

    return {
        "ok": True
    }


# =========================================
# MAIN
# =========================================

if __name__ == "__main__":
    app.run(debug=True)