from datetime import UTC, datetime

from octopus_mcp.cache.db import open_db
from octopus_mcp.cache.rates import RateRow, RatesRepo


def test_upsert_and_read_unit_rates() -> None:
    repo = RatesRepo(open_db(":memory:"))
    repo.upsert_unit_rates(
        tariff_code="E-1R-VAR-22-11-01-A",
        fuel="electricity",
        rows=[
            RateRow(
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 4, 1, tzinfo=UTC),
                value_inc_vat=26.25,
                value_exc_vat=25.0,
            ),
            RateRow(
                valid_from=datetime(2026, 4, 1, tzinfo=UTC),
                valid_to=None,
                value_inc_vat=25.20,
                value_exc_vat=24.0,
            ),
        ],
    )
    rows = repo.get_unit_rates("E-1R-VAR-22-11-01-A")
    assert len(rows) == 2
    assert rows[0].value_inc_vat == 26.25


def test_upsert_standing_charges() -> None:
    repo = RatesRepo(open_db(":memory:"))
    repo.upsert_standing_charges(
        tariff_code="E-1R-VAR-22-11-01-A",
        fuel="electricity",
        rows=[
            RateRow(
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=None,
                value_inc_vat=50.0,
                value_exc_vat=47.62,
            )
        ],
    )
    rows = repo.get_standing_charges("E-1R-VAR-22-11-01-A")
    assert rows[0].value_inc_vat == 50.0


# --- payment methods (second review: choose per window, keep history, be configurable) ---


def _rr(day: int, value: float, method: str) -> "RateRow":
    from datetime import UTC, datetime

    from octopus_mcp.cache.rates import RateRow

    return RateRow(
        valid_from=datetime(2026, 9, day, tzinfo=UTC),
        valid_to=datetime(2026, 9, day + 1, tzinfo=UTC),
        value_inc_vat=value,
        value_exc_vat=value,
        payment_method=method,
    )


def test_payment_method_is_chosen_per_window_and_single_method_windows_survive() -> None:
    from octopus_mcp.cache.db import open_db
    from octopus_mcp.cache.rates import RatesRepo

    repo = RatesRepo(open_db(":memory:"), prefer="DIRECT_DEBIT")
    repo.upsert_unit_rates(
        tariff_code="T",
        fuel="gas",
        rows=[
            _rr(1, 8.1, "DIRECT_DEBIT"),
            _rr(1, 8.5, "NON_DIRECT_DEBIT"),  # same window: must not overwrite
            _rr(2, 9.0, "NON_DIRECT_DEBIT"),  # published under one method only: kept
        ],
    )
    assert [r.value_inc_vat for r in repo.get_unit_rates("T")] == [8.1, 9.0]
    assert repo.has_payment_choice("T")


def test_non_direct_debit_preference_is_honoured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from octopus_mcp.cache.db import open_db
    from octopus_mcp.cache.rates import RatesRepo

    monkeypatch.setenv("OCTOPUS_PAYMENT_METHOD", "non_direct_debit")
    repo = RatesRepo(open_db(":memory:"))
    repo.upsert_unit_rates(
        tariff_code="T",
        fuel="gas",
        rows=[_rr(1, 8.1, "DIRECT_DEBIT"), _rr(1, 8.5, "NON_DIRECT_DEBIT")],
    )
    assert repo.prefer == "NON_DIRECT_DEBIT"
    assert [r.value_inc_vat for r in repo.get_unit_rates("T")] == [8.5]


def test_migration_003_keeps_existing_rate_history() -> None:
    """Rates beyond the newest API page cannot be re-fetched, so 003 must not drop them."""
    import sqlite3

    from octopus_mcp.cache.db import _list_migrations, _split_sql, run_migrations
    from octopus_mcp.cache.rates import RatesRepo

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for version, sql in _list_migrations():
        if version > 2:
            break
        for statement in _split_sql(sql):
            conn.execute(statement)
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO unit_rates VALUES ('T','gas','2024-01-01T00:00:00+00:00',NULL,7.0,7.0)"
    )
    conn.execute("INSERT INTO sync_state VALUES ('rates:gas:T','2026-10-03T00:00:00+00:00',86400)")
    conn.commit()

    run_migrations(conn)

    rows = RatesRepo(conn, prefer="DIRECT_DEBIT").get_unit_rates("T")
    assert [(r.value_inc_vat, r.payment_method) for r in rows] == [(7.0, "")]
    assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 0  # re-fetch
