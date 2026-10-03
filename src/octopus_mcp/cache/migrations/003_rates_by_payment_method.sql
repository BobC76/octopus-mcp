-- Variable tariffs publish a DIRECT_DEBIT and a NON_DIRECT_DEBIT rate for the same
-- window. 001 keyed rates on (tariff_code, valid_from), so whichever was written last
-- silently replaced the other. Add payment_method to the key and choose at read time.
--
-- Existing rows are KEPT, marked '' (method unknown): the rate endpoints return only
-- the newest page, so history older than that cannot be re-fetched. For windows that
-- are re-fetched, the explicit DIRECT_DEBIT / NON_DIRECT_DEBIT rows outrank ''.
CREATE TABLE unit_rates_new (
  tariff_code    TEXT NOT NULL,
  fuel           TEXT NOT NULL,
  valid_from     TEXT NOT NULL,
  valid_to       TEXT,
  value_inc_vat  REAL NOT NULL,
  value_exc_vat  REAL NOT NULL,
  payment_method TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tariff_code, valid_from, payment_method)
);
INSERT INTO unit_rates_new (tariff_code, fuel, valid_from, valid_to, value_inc_vat, value_exc_vat)
  SELECT tariff_code, fuel, valid_from, valid_to, value_inc_vat, value_exc_vat FROM unit_rates;
DROP TABLE unit_rates;
ALTER TABLE unit_rates_new RENAME TO unit_rates;

CREATE TABLE standing_charges_new (
  tariff_code    TEXT NOT NULL,
  fuel           TEXT NOT NULL,
  valid_from     TEXT NOT NULL,
  valid_to       TEXT,
  value_inc_vat  REAL NOT NULL,
  value_exc_vat  REAL NOT NULL,
  payment_method TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tariff_code, valid_from, payment_method)
);
INSERT INTO standing_charges_new (tariff_code, fuel, valid_from, valid_to, value_inc_vat, value_exc_vat)
  SELECT tariff_code, fuel, valid_from, valid_to, value_inc_vat, value_exc_vat FROM standing_charges;
DROP TABLE standing_charges;
ALTER TABLE standing_charges_new RENAME TO standing_charges;

-- Re-fetch so the current page gains explicit payment methods.
DELETE FROM sync_state WHERE resource LIKE 'rates:%';
