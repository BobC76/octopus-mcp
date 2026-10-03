-- Tariff agreements must be kept per direction. 001 keyed them on
-- (account_number, fuel, valid_from) with no meter or direction, so an import
-- and an export agreement were indistinguishable, and two starting on the same
-- day overwrote each other. This is a cache: rebuild it and force the account
-- to be re-fetched rather than trying to recover direction from old rows.
DROP TABLE tariff_assignments;

CREATE TABLE tariff_assignments (
  account_number TEXT NOT NULL,
  fuel           TEXT NOT NULL,
  is_export      INTEGER NOT NULL DEFAULT 0,
  product_code   TEXT NOT NULL,
  tariff_code    TEXT NOT NULL,
  valid_from     TEXT NOT NULL,
  valid_to       TEXT,
  PRIMARY KEY (account_number, fuel, is_export, valid_from)
);

DELETE FROM sync_state WHERE resource LIKE 'account:%';
