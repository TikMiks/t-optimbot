import asyncio
import tempfile
from pathlib import Path

from bot.db import Database, PromoCodeExists
from bot.platega import extract_promo_hint, match_known_promo, rubles_to_kopecks
from bot.utils import fmt_money, normalize_code, parse_money


async def main() -> None:
    assert parse_money("500") == 50000
    assert parse_money("500,50") == 50050
    assert parse_money("500.50") == 50050
    assert parse_money("1 000") == 100000
    assert parse_money("500₽") == 50000
    for bad in ("-5", "abc", ""):
        try:
            parse_money(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parse_money({bad!r}) должен падать")
    assert fmt_money(0) == "0 ₽"
    assert fmt_money(50000) == "500 ₽"
    assert fmt_money(123456) == "1 234,56 ₽"
    assert normalize_code(" neon15 ") == "NEON15"
    assert extract_promo_hint('{"promo":"NEON15"}') == "NEON15"
    assert match_known_promo(["оплата neon15"], ["NEON15"]) == "NEON15"
    assert rubles_to_kopecks(1.15) == 115

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        await db.connect()
        promo = await db.create_promo("NEON15", 50000)
        token = promo.bind_token
        assert token
        await db.bind_owner(promo.id, 100, "nick", "Nick")
        await db.add_sale(promo.id, 50000, amount=450000, note="one")
        await db.add_sale(promo.id, 50000)
        await db.add_sale(promo.id, 50000, external_id="tx-1")
        stats = await db.promo_stats(promo.id)
        assert stats.sales_total == 3
        assert stats.earned == 150000
        assert stats.balance == 150000
        await db.add_payout(promo.id, 100000)
        stats = await db.promo_stats(promo.id)
        assert stats.balance == 50000
        assert stats.last_payout_amount == 100000
        assert stats.sales_7d == 3
        assert stats.sales_30d == 3
        deleted = await db.delete_last_sale(promo.id)
        assert deleted is not None
        stats = await db.promo_stats(promo.id)
        assert stats.sales_total == 2
        try:
            await db.create_promo("NEON15", 50000)
        except PromoCodeExists:
            pass
        else:
            raise AssertionError("дубликат промокода не отвергнут")
        payment = await db.upsert_payment(
            transaction_id="tx-charge",
            amount=450000,
            currency="RUB",
            status="CONFIRMED",
            payload='{"promo":"NEON15"}',
            description=None,
            payment_method="SBPQR",
            promo_code="NEON15",
            platega_created_at=None,
            created_at=1,
        )
        sale_id = await db.add_sale(
            promo.id, 50000, amount=450000, external_id="tx-charge"
        )
        await db.mark_payment_processed(
            "tx-charge", promo_id=promo.id, sale_id=sale_id, promo_code="NEON15"
        )
        reverted = await db.revert_sale(sale_id)
        assert reverted is not None
        stats = await db.promo_stats(promo.id)
        assert stats.sales_total == 2
        await db.close()
    print("smoke_ok")


if __name__ == "__main__":
    asyncio.run(main())
