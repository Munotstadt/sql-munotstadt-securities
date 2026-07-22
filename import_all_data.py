"""
Backfill-Import: liest all_data.csv und schreibt historische Kurse in
security_prices. Findet die passende SecurityID über MainID (numerisch)
oder über den Namen (MainNameLocal), falls MainID im CSV kein numerischer
Wert ist. Fehlende Securities werden automatisch in security_master
angelegt (MainID = 0 als Platzhalter, analog zum bisherigen Vorgehen).

CSV-Spalten: ValueDate,ValueDefinitionID,AmountLC,ValueComment,SourceComments,MainID,MainNameLocal,DateAdded
Datumsformat: dd.mm.yyyy HH:MM:SS

Ausführbar mehrfach (idempotent) dank MERGE - keine Duplikate bei erneutem Lauf.
"""

import csv
import os
from datetime import datetime

import pyodbc

CSV_PATH = "all_data.csv"

# Namens-Aliase: CSV-Name -> bereits bestehender Name in security_master.
# Verhindert, dass für bekannte Varianten/Schreibweisen neue Securities angelegt werden.
NAME_ALIASES = {
    "ishares core ftse 100 (gbp)": "ishares core ftse 100",
}


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


def load_security_master(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT SecurityID, MainID, Name FROM security_master")
    by_mainid = {}
    by_name = {}
    for row in cursor.fetchall():
        if row.MainID and row.MainID != 0:
            by_mainid[row.MainID] = row.SecurityID
        by_name[row.Name.strip().lower()] = row.SecurityID
    return by_mainid, by_name


def get_or_create_security(conn, by_mainid, by_name, main_id_raw, name):
    """Liefert SecurityID; legt bei Bedarf eine neue security_master-Zeile an."""
    # 1) Versuch: numerische MainID
    try:
        main_id_num = int(main_id_raw)
        if main_id_num in by_mainid:
            return by_mainid[main_id_num]
    except (ValueError, TypeError):
        pass

    # 2) Versuch: Name-Match (case-insensitive), inkl. Alias-Auflösung
    key = name.strip().lower()
    key = NAME_ALIASES.get(key, key)
    if key in by_name:
        return by_name[key]

    # 3) Nicht gefunden -> neue Security anlegen (MainID = 0, wie bisheriges Vorgehen)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO security_master (MainID, Name, IsActive) "
        "OUTPUT INSERTED.SecurityID VALUES (0, ?, 1)",
        name,
    )
    new_id = cursor.fetchone()[0]
    conn.commit()
    by_name[key] = new_id
    print(f"[NEU] Security angelegt: '{name}' -> SecurityID {new_id} (MainID=0)")
    return new_id


def parse_value_date(raw):
    return datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M:%S")


def upsert_price(conn, security_id, value_date, price, source):
    cursor = conn.cursor()
    cursor.execute(
        """
        MERGE security_prices AS target
        USING (SELECT ? AS SecurityID, ? AS ValueDate) AS src
            ON target.SecurityID = src.SecurityID AND target.ValueDate = src.ValueDate
        WHEN MATCHED THEN
            UPDATE SET PriceLC = ?, Source = ?
        WHEN NOT MATCHED THEN
            INSERT (SecurityID, ValueDate, PriceLC, Source)
            VALUES (?, ?, ?, ?);
        """,
        security_id, value_date,
        price, source,
        security_id, value_date, price, source,
    )


def main():
    conn = get_connection()
    by_mainid, by_name = load_security_master(conn)

    imported = 0
    skipped = 0

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                security_id = get_or_create_security(
                    conn, by_mainid, by_name,
                    row["MainID"], row["MainNameLocal"]
                )
                value_date = parse_value_date(row["ValueDate"])
                price = float(row["AmountLC"])
                source = (row.get("SourceComments") or "").strip() or None

                upsert_price(conn, security_id, value_date, price, source)
                imported += 1

            except Exception as e:
                print(f"[ERROR] Zeile übersprungen ({row.get('MainNameLocal')}, {row.get('ValueDate')}): {e}")
                skipped += 1

    conn.commit()
    conn.close()
    print(f"\nFertig: {imported} Zeilen importiert, {skipped} übersprungen.")


if __name__ == "__main__":
    main()
