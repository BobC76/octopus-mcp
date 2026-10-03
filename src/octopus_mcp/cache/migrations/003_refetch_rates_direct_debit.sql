-- Rates cached before 003 may hold the NON_DIRECT_DEBIT price: the API returns both
-- payment methods for the same window and the (tariff_code, valid_from) key kept
-- whichever was written last. Rates are a cache - drop them and let them be re-fetched.
DELETE FROM unit_rates;
DELETE FROM standing_charges;
DELETE FROM sync_state WHERE resource LIKE 'rates:%';
