/* ============================================================
   security_master anpassen:
   - Entfernen: ISIN, Ticker, SecurityType, AssetClass, Currency,
                Exchange, Country, Sector, ModifiedDate
   - Umbenennen: CreatedDate -> Created_at
   - Neu: MainID, Comment, ValidFrom, ValidTo, Modified_at
   ============================================================ */

-- 1) UNIQUE-Constraint auf ISIN zuerst entfernen (sonst blockiert DROP COLUMN)
DECLARE @constraintName NVARCHAR(200);
SELECT @constraintName = kc.name
FROM sys.key_constraints kc
JOIN sys.index_columns ic ON kc.parent_object_id = ic.object_id AND kc.unique_index_id = ic.index_id
JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
WHERE kc.parent_object_id = OBJECT_ID('security_master') AND c.name = 'ISIN';

IF @constraintName IS NOT NULL
    EXEC('ALTER TABLE security_master DROP CONSTRAINT ' + @constraintName);

-- 2) Alte Spalten entfernen
ALTER TABLE security_master
    DROP COLUMN ISIN, Ticker, SecurityType, AssetClass, Currency, Exchange, Country, Sector;

-- 3) CreatedDate umbenennen zu Created_at
EXEC sp_rename 'security_master.CreatedDate', 'Created_at', 'COLUMN';

-- 4) ModifiedDate entfernen (wird durch Modified_at ersetzt)
ALTER TABLE security_master
    DROP COLUMN ModifiedDate;

-- 5) Neue Spalten hinzufügen
ALTER TABLE security_master ADD
    MainID      INT NOT NULL,
    Comment     NVARCHAR(500) NULL,
    ValidFrom   DATE NULL,
    ValidTo     DATE NULL,
    Modified_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME();

-- 6) Kontrolle: finale Spaltenstruktur anzeigen
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'security_master'
ORDER BY ORDINAL_POSITION;
