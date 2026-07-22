"""
Collector: SNB (R10), Raiffeisen Winterthur Hypothekarzinsen, onvista (STOXX Europe 600 EUR NR)
Schreibt direkt in Azure SQL security_prices (statt CSV wie in den Original-Collectors).

Zuordnung Name -> SecurityID (security_master):
    STOXX Europe 600 EUR NR                                      -> SecurityID 4
    Rendite Bundesobligationen Eidgenossenschaft 10 Jahre (%)     -> SecurityID 43
    Raiffeisen Winterthur Hypothek 1 Jahr Zinssatz                -> SecurityID 44
    Raiffeisen Winterthur Hypothek 5 Jahr Zinssatz                -> SecurityID 45
    Raiffeisen Winterthur Hypothek 10 Jahr Zinssatz               -> SecurityID 46
    Raiffeisen Winterthur Hypothek 15 Jahr Zinssatz               -> SecurityID 47

Die SecurityID wird zur Laufzeit per Name-Lookup aus security_master geholt
(robuster als hartkodierte IDs, falls sich die Tabelle mal ändert).
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import pyodbc

# ---------------------------------------------------------------------------
# DB-Verbindung
# ---------------------------------------------------------------------------

def get_connection():
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.environ['SQL_SERVER']};"
        f"DATABASE={os.environ['SQL_DATABASE']};"
        f"UID={os.environ['SQL_USER']};"
        f"PWD={os.environ['SQL_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)


def get_security_id_map(conn, names):
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in names)
    cursor.execute(
        f"SELECT Name, SecurityID FROM security_master WHERE Name IN ({placeholders})",
        *names,
    )
    return {row.Name: row.SecurityID for row in cursor.fetchall()}


def upsert_price(conn, security_id, value_date, price, currency, source):
    cursor = conn.cursor()
    cursor.execute(
        """
        MERGE security_prices AS target
        USING (SELECT ? AS SecurityID, ? AS ValueDate) AS src
            ON target.SecurityID = src.SecurityID AND target.ValueDate = src.ValueDate
        WHEN MATCHED THEN
            UPDATE SET PriceLC = ?, Currency = ?, Source = ?
        WHEN NOT MATCHED THEN
            INSERT (SecurityID, ValueDate, PriceLC, Currency, Source)
            VALUES (?, ?, ?, ?, ?);
        """,
        security_id, value_date,
        price, currency, source,
        security_id, value_date, price, currency, source,
    )
    conn.commit()


def cleanup_today(conn):
    """Pro SecurityID/Tag nur High, Low und zeitlich letzten Kurs behalten."""
    cursor = conn.cursor()
    cursor.execute(
        """
        ;WITH Today AS (
            SELECT SecurityID, ValueDate,
                ROW_NUMBER() OVER (PARTITION BY SecurityID ORDER BY PriceLC DESC, ValueDate DESC) AS RnMax,
                ROW_NUMBER() OVER (PARTITION BY SecurityID ORDER BY PriceLC ASC, ValueDate DESC)  AS RnMin,
                ROW_NUMBER() OVER (PARTITION BY SecurityID ORDER BY ValueDate DESC)                AS RnLast
            FROM security_prices
            WHERE CAST(ValueDate AS DATE) = CAST(SYSUTCDATETIME() AS DATE)
        )
        DELETE sp
        FROM security_prices sp
        JOIN Today t
            ON sp.SecurityID = t.SecurityID AND sp.ValueDate = t.ValueDate
        WHERE t.RnMax <> 1 AND t.RnMin <> 1 AND t.RnLast <> 1;
        """
    )
    print(f"Cleanup: {cursor.rowcount} überzählige Zeilen für heute gelöscht.")
    conn.commit()


# ---------------------------------------------------------------------------
# Quelle 1: SNB RSS-Feed (R10 - Rendite Bundesobligationen 10J)
# ---------------------------------------------------------------------------

SNB_RSS_URL = "https://www.snb.ch/public/de/rss/interestRates"


def fetch_snb_r10():
    req = urllib.request.Request(SNB_RSS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml = resp.read().decode("utf-8", errors="replace")

    for item in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        if re.search(r"<cb:rateName>\s*R10\s*</cb:rateName>", item):
            value = float(
                re.search(r"<cb:value>\s*([\d,.\-]+)\s*</cb:value>", item)
                .group(1).replace(",", ".")
            )
            return value
    raise ValueError("R10 nicht im SNB RSS-Feed gefunden")


# ---------------------------------------------------------------------------
# Quelle 2: Raiffeisen API (Hypothekarzinsen Winterthur)
# ---------------------------------------------------------------------------

RAIFFEISEN_API_URL = "https://api.raiffeisen.ch/loan-product-service/v1/products"
RAIFFEISEN_BANK_CODE = "1485"

RAIFFEISEN_DURATION_MAP = {
    12:  "Raiffeisen Winterthur Hypothek 1 Jahr Zinssatz",
    60:  "Raiffeisen Winterthur Hypothek 5 Jahr Zinssatz",
    120: "Raiffeisen Winterthur Hypothek 10 Jahr Zinssatz",
    180: "Raiffeisen Winterthur Hypothek 15 Jahr Zinssatz",
}


def fetch_raiffeisen_rates():
    req = urllib.request.Request(RAIFFEISEN_API_URL, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-rai-bankcode": RAIFFEISEN_BANK_CODE,
        "x-rai-channel": "INFORMATION",
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.raiffeisen.ch/winterthur/de/privatkunden/"
                   "wohnen-und-hypotheken/hypothekarzinsen.html",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    fixed = next(p for p in data if p.get("type") == "FIXED")
    rates_by_months = {
        v["durationInMonths"]: v["rate"]
        for v in fixed["variants"]
        if v["durationInMonths"] in RAIFFEISEN_DURATION_MAP
    }
    # Name -> Rate
    return {
        name: rates_by_months[months]
        for months, name in RAIFFEISEN_DURATION_MAP.items()
        if months in rates_by_months
    }


# ---------------------------------------------------------------------------
# Quelle 3: onvista (STOXX Europe 600 EUR NR)
# ---------------------------------------------------------------------------

ONVISTA_API_URL = "https://api.onvista.de/api/v1/instruments/INDEX/1544657/quote?idNotation=&range=D1"
ONVISTA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_onvista_stoxx():
    req = urllib.request.Request(ONVISTA_API_URL, headers=ONVISTA_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read())
    price = float(d.get("last") or d.get("previousLast"))
    return price


# ---------------------------------------------------------------------------
# Quelle 4: Raiffeisen Futura II Fonds (boerse.raiffeisen.ch, HTML-Scraping)
# ---------------------------------------------------------------------------
# CAUTION: kein dokumentiertes JSON-API für diese Fonds (anders als die
# Hypothekarzinsen) - die Seite rendert NAV + Datum direkt im HTML. Falls
# Raiffeisen das Seitenlayout ändert, muss der Regex unten evtl. angepasst werden.

RAIFFEISEN_FUTURA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

RAIFFEISEN_FUTURA_FUNDS = [
    {
        "fund_id": "114426954",
        "name": "Raiffeisen Futura II - Systematic Invest Equity (Vorsorge)",
    },
    {
        "fund_id": "114426952",
        "name": "Raiffeisen Futura II - Systematic Invest Equity B (Samantha)",
    },
]


def strip_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def fetch_raiffeisen_futura_nav(fund_id):
    """Liefert (price, value_date) für einen Raiffeisen-Futura-Fonds."""
    url = f"https://boerse.raiffeisen.ch/fonds/detail/{fund_id}?exchangeid=393"
    req = urllib.request.Request(url, headers=RAIFFEISEN_FUTURA_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    text = strip_html(html)

    # Sucht: CHF <Kurs> ... <Veränderung>% (<abs. Veränderung>) <DD.MM.YYYY>
    match = re.search(
        r"CHF\s*\n*\s*([\d]+[.,]\d+)\s*\n*\s*[+\-]?[\d.,]+%\s*\([+\-]?[\d.,]+\)\s*(\d{2}\.\d{2}\.\d{4})",
        text,
    )
    if not match:
        raise ValueError(
            f"NAV-Kurs/Datum-Muster auf der Raiffeisen-Fondsseite (fund_id={fund_id}) "
            "nicht gefunden - Seitenlayout hat sich evtl. geändert."
        )

    price = float(match.group(1).replace(",", "."))
    date_str = match.group(2)
    value_date = datetime.strptime(date_str, "%d.%m.%Y").replace(hour=18, minute=0)
    return price, value_date


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    names = [
        "STOXX Europe 600 EUR NR",
        "Rendite Bundesobligationen Eidgenossenschaft 10 Jahre (%)",
        "Raiffeisen Winterthur Hypothek 1 Jahr Zinssatz",
        "Raiffeisen Winterthur Hypothek 5 Jahr Zinssatz",
        "Raiffeisen Winterthur Hypothek 10 Jahr Zinssatz",
        "Raiffeisen Winterthur Hypothek 15 Jahr Zinssatz",
        "Raiffeisen Futura II - Systematic Invest Equity (Vorsorge)",
        "Raiffeisen Futura II - Systematic Invest Equity B (Samantha)",
    ]

    conn = get_connection()
    id_map = get_security_id_map(conn, names)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    errors = 0

    # --- SNB R10 ---
    try:
        value = fetch_snb_r10()
        name = "Rendite Bundesobligationen Eidgenossenschaft 10 Jahre (%)"
        upsert_price(conn, id_map[name], now, value, None, "SNB")
        print(f"[OK] {name}: {value}")
    except Exception as e:
        print(f"[ERROR] SNB R10: {e}")
        errors += 1

    # --- Raiffeisen Hypothekarzinsen ---
    try:
        rates = fetch_raiffeisen_rates()
        for name, rate in rates.items():
            upsert_price(conn, id_map[name], now, rate, None, "RB Winterthur")
            print(f"[OK] {name}: {rate}")
    except Exception as e:
        print(f"[ERROR] Raiffeisen Hypothekarzinsen: {e}")
        errors += 1

    # --- onvista STOXX Europe 600 EUR NR ---
    try:
        price = fetch_onvista_stoxx()
        name = "STOXX Europe 600 EUR NR"
        upsert_price(conn, id_map[name], now, price, "EUR", "onvista")
        print(f"[OK] {name}: {price}")
    except Exception as e:
        print(f"[ERROR] onvista STOXX Europe 600 EUR NR: {e}")
        errors += 1

    # --- Raiffeisen Futura II Fonds (Vorsorge + Samantha) ---
    for fund in RAIFFEISEN_FUTURA_FUNDS:
        try:
            price, value_date = fetch_raiffeisen_futura_nav(fund["fund_id"])
            name = fund["name"]
            upsert_price(conn, id_map[name], value_date, price, "CHF", "Raiffeisen Börse")
            print(f"[OK] {name}: {price} CHF (NAV-Datum: {value_date})")
        except Exception as e:
            print(f"[ERROR] {fund['name']} (fund_id={fund['fund_id']}): {e}")
            errors += 1

    cleanup_today(conn)
    conn.close()

    if errors:
        print(f"\n{errors} Quelle(n) konnten nicht abgerufen werden.")


if __name__ == "__main__":
    main()
