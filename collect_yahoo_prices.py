"""
Holt für alle Securities mit gesetztem Yahoo-Ticker den aktuellen
Kurs von Yahoo Finance und schreibt ihn in security_prices (MERGE = Upsert,
damit mehrfache Läufe innerhalb der gleichen Minute keinen PK-Konflikt geben).

Datenquelle für die Ticker-Zuordnung: security_parameter_log
  - ParameterID 20 = DataSource        (Wert 'Yahoo Finance' markiert aktive Quelle)
  - ParameterID 21 = DataSourceTicker  (Wert = eigentlicher Yahoo-Ticker, z.B. '^TNX')
Eine Security gilt als "aktiv fuer Yahoo", wenn ihre ParameterID=20-Zeile
ParameterValue = 'Yahoo Finance' hat und ValidTo entweder NULL ist oder in
der Zukunft liegt. Der Ticker kommt aus der zugehoerigen ParameterID=21-Zeile
mit der gleichen Bedingung.
"""

import os
import sys
from datetime import datetime, timezone
from urllib.parse import unquote

import pyodbc
import yfinance as yf


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


def fetch_tickers(conn):
    """
    Alle SecurityID + Yahoo-Ticker, die aktuell gueltig sind.
    Quelle: security_parameter_log (ParameterID 20 = DataSource, 21 = DataSourceTicker).
    Kein Bezug mehr auf die alte Tabelle security_parameters.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        ;WITH ActiveDataSource AS (
            SELECT SecurityID
            FROM security_parameter_log
            WHERE ParameterID = 20
              AND ParameterValue = 'Yahoo Finance'
              AND (ValidTo IS NULL OR ValidTo >= CAST(GETDATE() AS DATE))
        ),
        ActiveTicker AS (
            SELECT SecurityID, ParameterValue AS Ticker,
                   ROW_NUMBER() OVER (PARTITION BY SecurityID ORDER BY ValidFrom DESC) AS rn
            FROM security_parameter_log
            WHERE ParameterID = 21
              AND (ValidTo IS NULL OR ValidTo >= CAST(GETDATE() AS DATE))
        )
        SELECT ds.SecurityID, t.Ticker, m.Name
        FROM ActiveDataSource ds
        JOIN ActiveTicker t ON t.SecurityID = ds.SecurityID AND t.rn = 1
        JOIN security_master m ON m.SecurityID = ds.SecurityID
        """
    )
    return [(r.SecurityID, r.Ticker, r.Name) for r in cursor.fetchall()]


def get_price(ticker_encoded):
    """Ticker aus dem Parameter-Feld ist URL-encoded (z.B. %5E -> ^), daher decodieren."""
    ticker_symbol = unquote(ticker_encoded)
    t = yf.Ticker(ticker_symbol)

    data = t.history(period="1d", interval="1m")
    if data.empty:
        data = t.history(period="5d")
    if data.empty:
        return None, None

    last = data.iloc[-1]
    price = float(last["Close"])

    currency = None
    try:
        currency = t.fast_info.get("currency")
    except Exception:
        pass

    return price, currency


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
    """
    Reduziert security_prices für den heutigen (UTC-)Tag pro SecurityID auf
    maximal 3 Zeilen: höchster Kurs, tiefster Kurs, zeitlich letzter Kurs.
    Bei Überschneidungen (z.B. letzter Kurs = höchster Kurs) bleiben
    entsprechend weniger als 3 Zeilen übrig.
    """
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
    deleted = cursor.rowcount
    conn.commit()
    print(f"Cleanup: {deleted} überzählige Zeilen für heute gelöscht (nur High/Low/Last bleiben je Security).")


def main():
    conn = get_connection()
    tickers = fetch_tickers(conn)
    print(f"{len(tickers)} Securities mit aktivem Yahoo-Ticker gefunden.")

    # Auf Minute gerundet, damit MERGE bei mehrfachen Läufen sauber upsert statt dupliziert
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    errors = 0
    for security_id, ticker, name in tickers:
        try:
            price, currency = get_price(ticker)
            if price is None:
                print(f"[WARN] Keine Kursdaten für '{name}' (Ticker: {ticker})")
                errors += 1
                continue

            currency = (currency or "")[:3].upper() or None
            upsert_price(conn, security_id, now, price, currency, "Yahoo Finance")
            print(f"[OK] {name} ({ticker}): {price} {currency}")

        except Exception as e:
            print(f"[ERROR] {name} (Ticker: {ticker}): {e}")
            errors += 1

    cleanup_today(conn)
    conn.close()

    if errors:
        print(f"\n{errors} von {len(tickers)} Securities konnten nicht abgerufen werden.")
    sys.exit(0)  # bewusst kein Fehler-Exit, damit einzelne fehlende Ticker den Workflow nicht rot machen


if __name__ == "__main__":
    main()
