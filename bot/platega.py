"""Клиент Platega API: выгрузка транзакций и разбор промокода.

Документация: https://docs.platega.io/
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import aiohttp

from bot.utils import normalize_code

log = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELED = "CANCELED"
STATUS_CHARGEBACKED = "CHARGEBACKED"

INTERESTING_STATUSES = frozenset({STATUS_CONFIRMED, STATUS_CHARGEBACKED})

_PROMO_JSON_KEYS = ("promo", "promocode", "promo_code", "code", "coupon")
_KV_RE = re.compile(r"(?:promo(?:code)?|code)\s*[=:]\s*([^\s;|,]+)", re.IGNORECASE)
_PHRASE_RE = re.compile(r"по\s+промокоду\s+([^\s.,;:]+)", re.IGNORECASE)
_CODE_TOKEN_RE = re.compile(r"[\w\-]{2,32}", re.UNICODE)
_BUYER_RE = re.compile(
    r"(?:TG:|Telegram:\s*)@*([A-Za-z0-9_]{3,32})",
    re.IGNORECASE,
)


class PlategaError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class PlategaTransaction:
    transaction_id: str
    status: str
    amount_kopecks: int
    currency: str
    payload: str
    description: str
    payment_method: str
    created_at_raw: str
    created_at: int
    raw: dict[str, Any]


def rubles_to_kopecks(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Некорректная сумма Platega: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_platega_dt(raw: str | None) -> int:
    """Парсит createdAt из выгрузки. timeZoneId в запросе — UTC."""
    if not raw:
        return int(datetime.now(timezone.utc).timestamp())
    text = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(text.replace("+00:00", "Z"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    log.warning("Не разобрали дату транзакции Platega: %r", raw)
    return int(datetime.now(timezone.utc).timestamp())


def _candidate_from_text(text: str) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    try:
        return normalize_code(stripped)
    except ValueError:
        match = _KV_RE.search(stripped)
        if match:
            try:
                return normalize_code(match.group(1))
            except ValueError:
                return None
    return None


def extract_buyer_username(payload: str | None, description: str | None = None) -> str | None:
    """Достаёт Telegram-ник покупателя из payload (`TG:nick`) или описания."""
    for raw in (payload, description):
        if not raw:
            continue
        match = _BUYER_RE.search(str(raw))
        if match:
            return match.group(1)
    return None


def extract_promo_hint(payload: str | None, description: str | None = None) -> str | None:
    """Достаёт промокод из payload / description, если он там явно указан.

    На t-optim.cc сайт кладёт в payload Telegram покупателя (`TG:nick`),
    а промокод — в описание: «по промокоду MELL».
    """
    for raw in (payload, description):
        if not raw:
            continue
        text = str(raw).strip()
        phrase = _PHRASE_RE.search(text)
        if phrase:
            try:
                return normalize_code(phrase.group(1))
            except ValueError:
                pass
        if text.startswith("{") or text.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                for key in _PROMO_JSON_KEYS:
                    value = data.get(key)
                    if value:
                        try:
                            return normalize_code(str(value))
                        except ValueError:
                            continue
        # Голый промокод — только если строка целиком им и является.
        # «TG:username» и длинное описание сюда не попадают.
        if " " not in text and ":" not in text:
            hint = _candidate_from_text(text)
            if hint:
                return hint
    return None


def match_known_promo(texts: list[str], known_codes: list[str]) -> str | None:
    """Ищет известный промокод внутри произвольного текста (длинные коды первыми)."""
    if not known_codes:
        return None
    blob = " ".join(part for part in texts if part).upper()
    if not blob:
        return None
    tokens = set(_CODE_TOKEN_RE.findall(blob))
    for code in sorted(known_codes, key=len, reverse=True):
        if code.upper() in tokens or code.upper() in blob:
            return code
    return None


class PlategaClient:
    def __init__(self, merchant_id: str, secret: str, base_url: str) -> None:
        if not merchant_id or not secret:
            raise ValueError("PLATEGA_MERCHANT_ID и PLATEGA_SECRET обязательны")
        self.merchant_id = merchant_id
        self._secret = secret
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self._secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        own_session = session is None
        session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        try:
            async with session.request(
                method, url, headers=self._headers(), json=json_body
            ) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise PlategaError(
                        "Platega отклонила ключи (401). Проверьте MerchantId и Secret.",
                        status=401,
                        body=text[:500],
                    )
                if resp.status >= 400:
                    raise PlategaError(
                        f"Platega {method} {path} вернула {resp.status}",
                        status=resp.status,
                        body=text[:800],
                    )
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        finally:
            if own_session:
                await session.close()

    async def get_balances(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/balance/all")
        if isinstance(data, list):
            return data
        raise PlategaError("Неожиданный ответ /balance/all", body=str(data)[:300])

    async def export_transactions(
        self,
        date_from: datetime,
        date_to: datetime,
        statuses: list[str] | None = None,
    ) -> list[PlategaTransaction]:
        body: dict[str, Any] = {
            "from": date_from.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to": date_to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "timeZoneId": "UTC",
        }
        if statuses:
            body["statuses"] = statuses

        data = await self._request("POST", "/transaction/export/json", json_body=body)
        rows = await self._unwrap_export(data)
        return [self._normalize(row) for row in rows if self._row_id(row)]

    async def _unwrap_export(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            url = data.get("url") or data.get("fileUrl") or data.get("link")
            if url:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as session:
                    async with session.get(str(url)) as resp:
                        payload = await resp.json(content_type=None)
                if isinstance(payload, list):
                    return [row for row in payload if isinstance(row, dict)]
                if isinstance(payload, dict):
                    inner = payload.get("items") or payload.get("data") or payload.get("transactions")
                    if isinstance(inner, list):
                        return [row for row in inner if isinstance(row, dict)]
            for key in ("items", "data", "transactions"):
                inner = data.get(key)
                if isinstance(inner, list):
                    return [row for row in inner if isinstance(row, dict)]
        raise PlategaError("Неожиданный формат выгрузки транзакций", body=str(data)[:400])

    @staticmethod
    def _row_id(row: dict[str, Any]) -> str:
        return str(row.get("recordId") or row.get("id") or row.get("transactionId") or "").strip()

    def _normalize(self, row: dict[str, Any]) -> PlategaTransaction:
        raw_id = self._row_id(row)
        created_raw = str(row.get("createdAt") or row.get("created_at") or "")
        amount = row.get("amount") or 0
        try:
            kopecks = rubles_to_kopecks(amount)
        except ValueError:
            kopecks = 0
        return PlategaTransaction(
            transaction_id=raw_id,
            status=str(row.get("status") or "").upper(),
            amount_kopecks=kopecks,
            currency=str(row.get("currencyCode") or row.get("currency") or "RUB"),
            payload=str(row.get("payload") or ""),
            description=str(row.get("description") or ""),
            payment_method=str(row.get("paymentMethod") or ""),
            created_at_raw=created_raw,
            created_at=parse_platega_dt(created_raw),
            raw=row,
        )
