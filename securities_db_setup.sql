/* ============================================================
   Securities Datenbank - Setup Skript für Azure SQL
   Erstellt: Juli 2026
   Enthält: security_master, security_parameters, security_prices,
            security_distributions, security_values
   ============================================================ */

-- ==========================================
-- 1) Stammdaten
-- ==========================================
CREATE TABLE security_master (
    SecurityID      INT IDENTITY(1,1) PRIMARY KEY,
    ISIN            VARCHAR(12) NOT NULL UNIQUE,
    Ticker          VARCHAR(20),
    Name            NVARCHAR(200) NOT NULL,
    SecurityType    VARCHAR(30),        -- Equity, ETF, Bond, Crypto, Fund, Index
    AssetClass      VARCHAR(30),
    Currency        CHAR(3),
    Exchange        VARCHAR(50),
    Country         VARCHAR(50),
    Sector          VARCHAR(100),
    IsActive        BIT DEFAULT 1,
    CreatedDate     DATETIME2 DEFAULT SYSUTCDATETIME(),
    ModifiedDate    DATETIME2 DEFAULT SYSUTCDATETIME()
);

-- ==========================================
-- 2) Parameter- / Referenztabelle
--    (Metrik-Definitionen, Source-Mappings, Config)
-- ==========================================
CREATE TABLE security_parameters (
    ParameterID     INT IDENTITY(1,1) PRIMARY KEY,
    SecurityID      INT NULL FOREIGN KEY REFERENCES security_master(SecurityID),
                        -- NULL = globaler Parameter (z.B. Metrik-Definition)
                        -- gefüllt = security-spezifisch (z.B. Ticker-Mapping)
    ParameterName   VARCHAR(50) NOT NULL,
    ParameterType   VARCHAR(20) NOT NULL,   -- 'Metric', 'SourceMapping', 'Config'
    ParameterValue  NVARCHAR(200),
    Description     NVARCHAR(200),
    Source          VARCHAR(50),
    ValidFrom       DATE,
    ValidTo         DATE,
    CONSTRAINT UQ_SecParam UNIQUE (SecurityID, ParameterName, ValidFrom)
);

-- ==========================================
-- 3) Kurse (Zeitreihe)
-- ==========================================
CREATE TABLE security_prices (
    SecurityID      INT NOT NULL FOREIGN KEY REFERENCES security_master(SecurityID),
    ValueDate       DATETIME2 NOT NULL,
    OpenPrice       DECIMAL(18,6),
    HighPrice       DECIMAL(18,6),
    LowPrice        DECIMAL(18,6),
    ClosePrice      DECIMAL(18,6) NOT NULL,
    Currency        CHAR(3),
    Source          VARCHAR(50),
    CreatedDate     DATETIME2 DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (SecurityID, ValueDate)
);

-- ==========================================
-- 4) Ausschüttungen
-- ==========================================
CREATE TABLE security_distributions (
    DistributionID  INT IDENTITY(1,1) PRIMARY KEY,
    SecurityID      INT NOT NULL FOREIGN KEY REFERENCES security_master(SecurityID),
    ExDate          DATE NOT NULL,
    PayDate         DATE,
    Amount          DECIMAL(18,6) NOT NULL,
    Currency        CHAR(3),
    DistributionType VARCHAR(30),   -- Dividend, Interest, CapitalGain
    Source          VARCHAR(50),
    CreatedDate     DATETIME2 DEFAULT SYSUTCDATETIME()
);

-- ==========================================
-- 5) Kennzahlen (EAV, referenziert security_parameters)
-- ==========================================
CREATE TABLE security_values (
    SecurityID      INT NOT NULL FOREIGN KEY REFERENCES security_master(SecurityID),
    ValueDate       DATE NOT NULL,
    ParameterID     INT NOT NULL FOREIGN KEY REFERENCES security_parameters(ParameterID),
    MetricValue     DECIMAL(18,6) NOT NULL,
    Currency        CHAR(3),
    Source          VARCHAR(50),
    CreatedDate     DATETIME2 DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (SecurityID, ValueDate, ParameterID)
);

-- ==========================================
-- Indizes für häufige Abfragen
-- ==========================================
CREATE INDEX IX_prices_date ON security_prices(ValueDate);
CREATE INDEX IX_distributions_secid ON security_distributions(SecurityID, ExDate);
CREATE INDEX IX_values_paramid ON security_values(ParameterID);

-- ==========================================
-- Startwerte: globale Metrik-Definitionen
-- ==========================================
INSERT INTO security_parameters (SecurityID, ParameterName, ParameterType, ParameterValue, Description, Source)
VALUES
(NULL, 'MarketCap',        'Metric', 'CHF',   'Marktkapitalisierung',                 NULL),
(NULL, 'TER',               'Metric', '%',     'Total Expense Ratio (Fonds/ETF)',      NULL),
(NULL, 'PE',                'Metric', 'ratio', 'Kurs-Gewinn-Verhältnis (P/E)',         NULL),
(NULL, 'PB',                'Metric', 'ratio', 'Kurs-Buchwert-Verhältnis (P/B)',       NULL),
(NULL, 'DivYield',          'Metric', '%',     'Dividendenrendite',                    NULL),
(NULL, 'DivPayoutRatio',    'Metric', '%',     'Ausschüttungsquote',                   NULL),
(NULL, 'PriceTarget',       'Metric', 'CHF',   'Analysten Kursziel',                   NULL),
(NULL, 'AnalystRatingBuy',  'Metric', 'count', 'Anzahl Buy-Ratings',                   NULL),
(NULL, 'AnalystRatingHold', 'Metric', 'count', 'Anzahl Hold-Ratings',                  NULL),
(NULL, 'AnalystRatingSell', 'Metric', 'count', 'Anzahl Sell-Ratings',                  NULL),
(NULL, 'EPS',               'Metric', 'CHF',   'Gewinn pro Aktie',                     NULL),
(NULL, 'BookValuePerShare', 'Metric', 'CHF',   'Buchwert pro Aktie',                   NULL),
(NULL, 'DividendCoverage',  'Metric', 'ratio', 'Dividendendeckung',                    NULL),
(NULL, 'FreeFloat',         'Metric', '%',     'Free Float Anteil',                    NULL),
(NULL, 'IndexWeight',       'Metric', '%',     'Gewichtung im Index (z.B. SMI)',       NULL);

-- ==========================================
-- Startwerte: Config-Parameter
-- ==========================================
INSERT INTO security_parameters (SecurityID, ParameterName, ParameterType, ParameterValue, Description, Source)
VALUES
(NULL, 'UpdateFrequency', 'Config', 'Daily', 'Standard-Aktualisierungsfrequenz Kurse', NULL),
(NULL, 'PriceTolerance',  'Config', '1',     'Toleranz in CHF für Abgleiche',          NULL);

/* ============================================================
   Hinweis: SourceMapping-Parameter (z.B. YahooTicker, EODHDCode)
   erst einfügen, nachdem security_master befüllt ist und die
   SecurityID's feststehen. Beispiel:

   INSERT INTO security_parameters (SecurityID, ParameterName, ParameterType, ParameterValue, Source)
   VALUES (1, 'YahooTicker', 'SourceMapping', 'NESN.SW', 'Yahoo Finance');
   ============================================================ */
