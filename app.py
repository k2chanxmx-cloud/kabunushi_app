from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

tickets = [
    {
        "company": "すかいらーく",
        "balance": 3000,
        "expire_date": "2026-05-21",
        "category": "飲食",
    },
    {
        "company": "イオン",
        "balance": 10000,
        "expire_date": "2026-08-31",
        "category": "買い物",
    },
]


@app.route("/")
def home():
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

    tickets.append(
        {
            "company": company,
            "balance": balance,
            "expire_date": expire_date,
            "category": category,
        }
    )

    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)