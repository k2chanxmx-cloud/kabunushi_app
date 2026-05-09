import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id SERIAL PRIMARY KEY,
                    company TEXT NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 0,
                    expire_date DATE,
                    category TEXT NOT NULL DEFAULT 'その他',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        conn.commit()


@app.before_request
def before_request():
    init_db()


@app.route("/")
def home():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    id,
                    company,
                    balance,
                    expire_date,
                    category
                FROM tickets
                ORDER BY
                    expire_date IS NULL,
                    expire_date ASC,
                    id DESC;
            """)
            tickets = cur.fetchall()

    total_balance = sum(ticket["balance"] for ticket in tickets)
    ticket_count = len(tickets)

    return render_template(
        "index.html",
        tickets=tickets,
        total_balance=total_balance,
        ticket_count=ticket_count,
    )


@app.route("/add", methods=["POST"])
def add_ticket():
    company = request.form.get("company", "").strip()
    balance = request.form.get("balance", "0").strip()
    expire_date = request.form.get("expire_date", "").strip()
    category = request.form.get("category", "その他").strip()

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
                INSERT INTO tickets (company, balance, expire_date, category)
                VALUES (%s, %s, %s, %s);
            """, (company, balance, expire_date, category))
        conn.commit()

    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)