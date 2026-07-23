import pyodbc
import json
import os
from datetime import datetime, date

def json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)

def get_connection():
    server = os.environ["SQL_SERVER"]        # z.B. deinserver.database.windows.net
    database = os.environ["SQL_DATABASE"]
    username = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server=tcp:{server},1433;Database={database};"
        f"Uid={username};Pwd={password};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)

def query_to_list(cursor, query, params=None):
    cursor.execute(query, params or [])
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def write_json(filename, data):
    os.makedirs("data", exist_ok=True)
    with open(f"data/{filename}", "w", encoding="utf-8") as f:
        json.dump(data, f, default=json_default, ensure_ascii=False, indent=2)
    print(f"✓ {filename}: {len(data)} rows")

def main():
    conn = get_connection()
    cursor = conn.cursor()

    # Aktive Wertschriften
    securities = query_to_list(cursor, """
        SELECT SecurityID, MainID, Name, IsActive, Currency, Comment
        FROM security_master
        WHERE IsActive = 1
        ORDER BY Name
    """)
    write_json("securities.json", securities)

    # Preise letzte 400 Tage
    prices = query_to_list(cursor, """
        SELECT SecurityID, ValueDate, PriceLC, Currency, Source
        FROM security_prices
        WHERE ValueDate >= DATEADD(day, -400, SYSUTCDATETIME())
        ORDER BY SecurityID, ValueDate
    """)
    write_json("prices.json", prices)

    # Neuester Preis pro Security
    latest_prices = query_to_list(cursor, """
        SELECT p.SecurityID, p.ValueDate, p.PriceLC, p.Currency
        FROM security_prices p
        INNER JOIN (
            SELECT SecurityID, MAX(ValueDate) AS MaxDate
            FROM security_prices GROUP BY SecurityID
        ) latest ON p.SecurityID = latest.SecurityID AND p.ValueDate = latest.MaxDate
    """)
    write_json("latest_prices.json", latest_prices)

    # Ausschüttungen
    distributions = query_to_list(cursor, """
        SELECT DistributionID, SecurityID, ExDate, PayDate, Amount, Currency, DistributionType
        FROM security_distributions
        ORDER BY ExDate DESC
    """)
    write_json("distributions.json", distributions)

    # Kennzahlen
    values = query_to_list(cursor, """
        SELECT v.SecurityID, v.ValueDate, v.ParameterID, v.MetricValue, v.Currency
        FROM security_values v
        ORDER BY v.ValueDate DESC
    """)
    write_json("values.json", values)

    # Parameter-Log (u.a. ParameterID 59 = Export-Flag, 60 = Produkt, 61 = Subgruppe)
    parameter_log = query_to_list(cursor, """
        SELECT SecurityID, ParameterID, ParameterValue
        FROM security_parameter_log
    """)
    write_json("parameter_log.json", parameter_log)

    conn.close()
    print("Export abgeschlossen.")

if __name__ == "__main__":
    main()
