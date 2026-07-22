/* ============================================================
   Korrektur security_master:
   - ModifiedDate entfernen (falls noch vorhanden)
   - MainID, Comment, ValidFrom, ValidTo, Modified_at ergänzen
   - MainID wird für bestehende Zeilen mit SecurityID vorbefüllt,
     danach erst auf NOT NULL gesetzt (verträgt sich mit Daten)
   ============================================================ */

-- 1) Alte ModifiedDate-Spalte entfernen, falls noch vorhanden
IF COL_LENGTH('security_master', 'ModifiedDate') IS NOT NULL
    ALTER TABLE security_master DROP COLUMN ModifiedDate;

-- 2) Neue Spalten zunächst NULLABLE hinzufügen (erlaubt bei bestehenden Daten)
IF COL_LENGTH('security_master', 'MainID') IS NULL
    ALTER TABLE security_master ADD MainID INT NULL;

IF COL_LENGTH('security_master', 'Comment') IS NULL
    ALTER TABLE security_master ADD Comment NVARCHAR(500) NULL;

IF COL_LENGTH('security_master', 'ValidFrom') IS NULL
    ALTER TABLE security_master ADD ValidFrom DATE NULL;

IF COL_LENGTH('security_master', 'ValidTo') IS NULL
    ALTER TABLE security_master ADD ValidTo DATE NULL;

IF COL_LENGTH('security_master', 'Modified_at') IS NULL
    ALTER TABLE security_master ADD Modified_at DATETIME2 NULL DEFAULT SYSUTCDATETIME();

-- 3) Bestehende Zeilen befüllen (Fallback: MainID = SecurityID, kannst du später anpassen)
UPDATE security_master SET MainID = SecurityID WHERE MainID IS NULL;
UPDATE security_master SET Modified_at = SYSUTCDATETIME() WHERE Modified_at IS NULL;

-- 4) Jetzt erst auf NOT NULL setzen, da alle Zeilen befüllt sind
ALTER TABLE security_master ALTER COLUMN MainID INT NOT NULL;
ALTER TABLE security_master ALTER COLUMN Modified_at DATETIME2 NOT NULL;

-- 5) Kontrolle: finale Spaltenstruktur
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'security_master'
ORDER BY ORDINAL_POSITION;
