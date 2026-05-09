import os
import re
import json
import base64
import imaplib
import email
from email.header import decode_header
from email.message import EmailMessage

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


load_dotenv()

app = Flask(__name__)

# =========================================
# ENV
# =========================================

DATABASE_URL = os.environ.get("DATABASE_URL")

DOCOMO_IMAP_USER = os.environ.get("DOCOMO_IMAP_USER")
DOCOMO_IMAP_PASSWORD = os.environ.get("DOCOMO_IMAP_PASSWORD")
DOCOMO_IMAP_SERVER = os.environ.get(
    "DOCOMO_IMAP_SERVER",
    "imap.spmode.ne.jp"
)

YUTAI_GMAIL_TO = os.environ.get("YUTAI_GMAIL_TO")

GMAIL_TOKEN_JSON = os.environ.get("GMAIL_TOKEN_JSON")


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


# =========================================
# DB
# =========================================

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():

    with get_conn() as conn:

        with conn.cursor() as cur:

            # =====================================
            # tickets
            # =====================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tickets (

                    id SERIAL PRIMARY KEY,

                    company TEXT NOT NULL,

                    balance INTEGER NOT NULL DEFAULT 0,

                    expire_date DATE,

                    category TEXT NOT NULL DEFAULT 'その他',

                    source TEXT DEFAULT 'manual',

                    gmail_message_id TEXT,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cur.execute("""
                ALTER TABLE tickets
                ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual';
            """)

            cur.execute("""
                ALTER TABLE tickets
                ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;
            """)

            # =====================================
            # import_logs
            # =====================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS import_logs (

                    id SERIAL PRIMARY KEY,

                    status TEXT NOT NULL,

                    message TEXT,

                    imported_count INTEGER DEFAULT 0,

                    forwarded_count INTEGER DEFAULT 0,

                    deleted_count INTEGER DEFAULT 0,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cur.execute("""
                ALTER TABLE import_logs
                ADD COLUMN IF NOT EXISTS imported_count INTEGER DEFAULT 0;
            """)

            cur.execute("""
                ALTER TABLE import_logs
                ADD COLUMN IF NOT EXISTS forwarded_count INTEGER DEFAULT 0;
            """)

            cur.execute("""
                ALTER TABLE import_logs
                ADD COLUMN IF NOT EXISTS deleted_count INTEGER DEFAULT 0;
            """)

            # =====================================
            # docomo_forwarded
            # =====================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS docomo_forwarded (

                    id SERIAL PRIMARY KEY,

                    docomo_uid TEXT UNIQUE NOT NULL,

                    subject TEXT,

                    forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

        conn.commit()


@app.before_request
def before_request():
    init_db()


# =========================================
# Gmail
# =========================================

def get_gmail_service():

    if not GMAIL_TOKEN_JSON:
        raise Exception("GMAIL_TOKEN_JSON が未設定です")

    token_info = json.loads(GMAIL_TOKEN_JSON)

    creds = Credentials.from_authorized_user_info(
        token_info,
        SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds)


# =========================================
# Util
# =========================================

def save_import_log(
    status,
    message,
    imported_count=0,
    forwarded_count=0,
    deleted_count=0
):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO import_logs
                    (
                        status,
                        message,
                        imported_count,
                        forwarded_count,
                        deleted_count
                    )
                VALUES
                    (%s, %s, %s, %s, %s);
            """, (
                status,
                message,
                imported_count,
                forwarded_count,
                deleted_count
            ))

        conn.commit()


def decode_mime_words(value):

    if not value:
        return ""

    result = ""

    for part, charset in decode_header(value):

        if isinstance(part, bytes):

            result += part.decode(
                charset or "utf-8",
                errors="ignore"
            )

        else:
            result += part

    return result


def extract_body(msg):

    body = ""

    if msg.is_multipart():

        for part in msg.walk():

            content_type = part.get_content_type()

            disposition = str(
                part.get("Content-Disposition", "")
            )

            if "attachment" in disposition:
                continue

            payload = part.get_payload(decode=True)

            if not payload:
                continue

            charset = (
                part.get_content_charset()
                or "utf-8"
            )

            if content_type == "text/plain":

                body += payload.decode(
                    charset,
                    errors="ignore"
                )

    else:

        payload = msg.get_payload(decode=True)

        if payload:

            charset = (
                msg.get_content_charset()
                or "utf-8"
            )

            body = payload.decode(
                charset,
                errors="ignore"
            )

    return body


def is_yutai_mail(subject, body):

    text = subject + "\n" + body

    keywords = [
        "株主優待",
        "優待",
        "ご利用通知",
        "残高",
        "有効期限",
        "電子チケット",
        "クーポン",
        "優待券",
    ]

    return any(keyword in text for keyword in keywords)


def extract_ticket_info(subject, body):

    text = subject + "\n" + body

    company = "不明な優待"
    balance = 0
    expire_date = None

    known_companies = [
        "すかいらーく",
        "イオン",
        "ドトール",
        "マクドナルド",
        "吉野家",
        "コメダ",
        "くら寿司",
        "サイゼリヤ",
    ]

    for name in known_companies:

        if name in text:
            company = name
            break

    balance_patterns = [
        r"残高[:：]?\s*([0-9,]+)\s*円",
        r"([0-9,]+)\s*円分",
    ]

    for pattern in balance_patterns:

        match = re.search(pattern, text)

        if match:

            balance = int(
                match.group(1).replace(",", "")
            )

            break

    date_patterns = [
        r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
    ]

    for pattern in date_patterns:

        match = re.search(pattern, text)

        if match:

            y, m, d = match.groups()

            expire_date = (
                f"{int(y):04d}-"
                f"{int(m):02d}-"
                f"{int(d):02d}"
            )

            break

    return company, balance, expire_date


# =========================================
# Gmail Send
# =========================================

def send_to_gmail(service, subject, body):

    if not YUTAI_GMAIL_TO:
        raise Exception("YUTAI_GMAIL_TO 未設定")

    message = EmailMessage()

    message["To"] = YUTAI_GMAIL_TO

    message["Subject"] = (
        "[株主優待転送] " + subject
    )

    message.set_content(body)

    encoded = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": encoded}
    ).execute()


# =========================================
# Duplicate
# =========================================

def already_forwarded(docomo_uid):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT 1
                FROM docomo_forwarded
                WHERE docomo_uid = %s;
            """, (docomo_uid,))

            return cur.fetchone() is not None


def mark_forwarded(docomo_uid, subject):

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO docomo_forwarded
                    (docomo_uid, subject)
                VALUES
                    (%s, %s)
                ON CONFLICT (docomo_uid)
                DO NOTHING;
            """, (docomo_uid, subject))

        conn.commit()


# =========================================
# Docomo → Gmail
# =========================================

def forward_docomo_yutai_to_gmail():

    if (
        not DOCOMO_IMAP_USER
        or not DOCOMO_IMAP_PASSWORD
    ):
        raise Exception(
            "ドコモIMAP情報が未設定"
        )

    service = get_gmail_service()

    forwarded_count = 0

    mail = imaplib.IMAP4_SSL(
        DOCOMO_IMAP_SERVER,
        993
    )

    mail.login(
        DOCOMO_IMAP_USER,
        DOCOMO_IMAP_PASSWORD
    )

    mail.select("INBOX")

    status, data = mail.search(None, "ALL")

    if status != "OK":

        mail.logout()

        raise Exception(
            "ドコモメール検索失敗"
        )

    message_ids = data[0].split()

    for message_id in message_ids[-50:]:

        docomo_uid = message_id.decode()

        if already_forwarded(docomo_uid):
            continue

        status, msg_data = mail.fetch(
            message_id,
            "(RFC822)"
        )

        if status != "OK":
            continue

        raw_email = msg_data[0][1]

        msg = email.message_from_bytes(
            raw_email
        )

        subject = decode_mime_words(
            msg.get("Subject", "")
        )

        sender = decode_mime_words(
            msg.get("From", "")
        )

        body = extract_body(msg)

        if is_yutai_mail(subject, body):

            forward_body = f"""
From:
{sender}

Subject:
{subject}

----------------

{body}
"""

            send_to_gmail(
                service,
                subject,
                forward_body
            )

            forwarded_count += 1

        mark_forwarded(
            docomo_uid,
            subject
        )

    mail.logout()

    save_import_log(
        "success",
        "ドコモメール確認完了",
        forwarded_count=forwarded_count
    )


# =========================================
# Gmail → DB
# =========================================

def import_yutai_from_gmail():

    service = get_gmail_service()

    imported_count = 0
    deleted_count = 0

    result = service.users().messages().list(
        userId="me",
        q="newer_than:30d",
        maxResults=50
    ).execute()

    messages = result.get("messages", [])

    for item in messages:

        message_id = item["id"]

        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        headers = (
            msg.get("payload", {})
            .get("headers", [])
        )

        subject = ""

        for h in headers:

            if h.get("name") == "Subject":

                subject = h.get("value", "")
                break

        snippet = msg.get("snippet", "")

        if is_yutai_mail(subject, snippet):

            company, balance, expire_date = (
                extract_ticket_info(
                    subject,
                    snippet
                )
            )

            with get_conn() as conn:

                with conn.cursor() as cur:

                    cur.execute("""
                        SELECT 1
                        FROM tickets
                        WHERE gmail_message_id = %s;
                    """, (message_id,))

                    exists = cur.fetchone()

                    if not exists:

                        cur.execute("""
                            INSERT INTO tickets
                                (
                                    company,
                                    balance,
                                    expire_date,
                                    category,
                                    source,
                                    gmail_message_id
                                )
                            VALUES
                                (
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s
                                );
                        """, (
                            company,
                            balance,
                            expire_date,
                            "その他",
                            "gmail",
                            message_id
                        ))

                        imported_count += 1

                conn.commit()

        else:

            service.users().messages().trash(
                userId="me",
                id=message_id
            ).execute()

            deleted_count += 1

    save_import_log(
        "success",
        "Gmail取込完了",
        imported_count=imported_count,
        deleted_count=deleted_count
    )


# =========================================
# Routes
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
                    source
                FROM tickets
                ORDER BY id DESC;
            """)

            tickets = cur.fetchall()

            cur.execute("""
                SELECT
                    status,
                    message,
                    imported_count,
                    forwarded_count,
                    deleted_count,
                    created_at
                FROM import_logs
                ORDER BY id DESC
                LIMIT 10;
            """)

            import_logs = cur.fetchall()

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
        import_logs=import_logs
    )


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
                        source
                    )
                VALUES
                    (%s, %s, %s, %s, %s);
            """, (
                company,
                balance,
                expire_date,
                category,
                "manual"
            ))

        conn.commit()

    return redirect(url_for("home"))


@app.route("/forward-docomo", methods=["POST"])
def forward_docomo():

    try:

        forward_docomo_yutai_to_gmail()

    except Exception as e:

        save_import_log(
            "error",
            str(e)
        )

    return redirect(url_for("home"))


@app.route("/import-gmail", methods=["POST"])
def import_gmail():

    try:

        import_yutai_from_gmail()

    except Exception as e:

        save_import_log(
            "error",
            str(e)
        )

    return redirect(url_for("home"))


@app.route("/sync-all", methods=["POST"])
def sync_all():

    try:

        forward_docomo_yutai_to_gmail()

        import_yutai_from_gmail()

    except Exception as e:

        save_import_log(
            "error",
            str(e)
        )

    return redirect(url_for("home"))


# =========================================
# Main
# =========================================

if __name__ == "__main__":
    app.run(debug=True)