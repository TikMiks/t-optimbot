# Интеграция Platega: автоматическая регистрация покупок по промокоду

Документ описывает, как подключить платёжную систему **Platega** к боту партнёрской
программы t-optim.cc, чтобы продажа по промокоду попадала в таблицу `sales`
автоматически в момент успешной оплаты, без ручного ввода админом.

Источники (проверено, документация доступна):

| Что | URL |
| --- | --- |
| Авторизация, базовый URL | <https://docs.platega.io/> |
| Индекс всех страниц (машиночитаемый) | <https://docs.platega.io/llms.txt> |
| Создание платежа с заданным методом | `https://docs.platega.io/создание-платежной-ссылки-с-заданным-методом-29203843e0.md` |
| Создание платежа без заданного метода | `https://docs.platega.io/создание-платежной-ссылки-без-заданного-метода-33845703e0.md` |
| Проверка статуса платежа | `https://docs.platega.io/проверка-статуса-оплаты-платежа-29203844e0.md` |
| Callback об изменении статуса транзакции | `https://docs.platega.io/callback-об-изменении-статуса-транзакции-29209725e0.md` |
| Схема `CallbackPayload` | <https://docs.platega.io/callbackpayload-13226220d0.md> |
| Схема `CreateTransactionRequest` | <https://docs.platega.io/createtransactionrequest-13226217d0.md> |

> Кириллические URL выше приведены в читаемом виде; в браузере/коде они работают
> в percent-encoded форме. Удобнее заходить через `llms.txt` — там все ссылки готовы.

---

## 1. Спецификация API Platega

### 1.1. Базовый URL и версия

- **Платёжный API (нужен нам):** `https://app.platega.io`
- Версионирования в путях почти нет: основной эндпоинт `/transaction/process`,
  у «платёжной ссылки без заданного метода» — `/v2/transaction/process`.
- Отдельный **Payout API** (выводы на карты) живёт на другом хосте —
  `https://api.platega.io`, путь `/api/v1/...`, и у него **другая схема авторизации**
  (`Authorization: PG-HMAC ...`). Для нашей задачи он не нужен; главное не перепутать.
- Всё — JSON поверх HTTPS.

### 1.2. Авторизация запросов

Два заголовка на каждый запрос:

| Заголовок | Значение |
| --- | --- |
| `X-MerchantId` | ваш MerchantId (UUID) |
| `X-Secret` | ваш API-ключ |
| `Content-Type` | `application/json` |

Оба значения выдаёт менеджер при подключении и они же лежат в личном кабинете
my.platega.io → **Настройки**.

Ошибка `401` = неверные `X-MerchantId` / `X-Secret`.

### 1.3. Создание платежа

**`POST https://app.platega.io/transaction/process`** — способ оплаты задаём мы.

Тело запроса (`CreateTransactionRequest`):

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| `paymentMethod` | integer | да | Способ оплаты: `2` СБП (QR), `3` ЕРИП, `11` карточный эквайринг, `12` международная оплата, `13` криптовалюта, `14` SberPay |
| `paymentDetails.amount` | number (float) | да | Сумма платежа |
| `paymentDetails.currency` | string | да | Валюта, например `RUB` |
| `description` | string | да | Назначение платежа |
| `return` | string (uri) | да | Редирект при успешной оплате |
| `failedUrl` | string (uri) | да | Редирект при неуспешной оплате |
| `payload` | string | нет | **Произвольная строка. Единственное поле, которое возвращается в callback'е.** Сюда кладём промокод |
| `orderId` | string | нет | ID вашего внутреннего платежа |
| `metadata.userId` | string | нет* | Идентификатор плательщика (например, Telegram user ID). Используется антифродом |
| `metadata.userName` | string | нет* | Любые доп. данные о плательщике |

\* Для магазинов отдельных категорий `metadata` обязательна — уточните у менеджера.
Отсутствие `metadata.userId` при наличии требования **отключает антифрод-защиту и
может привести к отключению магазина**.

> **Поле `id` в запросе не передавать** — ID транзакции генерирует система.
> (В старой версии доки на gitbook `id` фигурировал — это устаревший пример.)

Пример запроса:

```http
POST /transaction/process HTTP/1.1
Host: app.platega.io
Content-Type: application/json
X-MerchantId: 29ef6fa6-0d2b-466c-9604-0363a30436cc
X-Secret: iStHENoXjHdy78A4tGG3M6TzqLvtRe335bbIGGYYx1Sf...
```

```json
{
  "paymentMethod": 2,
  "paymentDetails": { "amount": 2500, "currency": "RUB" },
  "description": "Настройка ПК t-optim.cc",
  "return": "https://t-optim.cc/pay/success",
  "failedUrl": "https://t-optim.cc/pay/fail",
  "payload": "{\"promo\":\"NICKNAME\",\"uid\":123456789}",
  "orderId": "to-10231",
  "metadata": { "userId": "123456789", "userName": "@username" }
}
```

Ответ `200` (`CreateTransactionResponse`):

```json
{
  "paymentMethod": "SBPQR",
  "transactionId": "3fa85f64-5717-4562-b3fc-2c463f66afa6",
  "redirect": "https://pay.platega.io?qrsbp",
  "return": "https://google.com",
  "paymentDetails": "100 RUB",
  "status": "PENDING",
  "expiresIn": "00:15:00",
  "merchantId": "1a021d91-9b26-4762-b303-5d4aac74e921",
  "usdtRate": 93.45
}
```

Ключевые поля ответа: `transactionId` (UUID, по нему потом матчим callback) и
`redirect` (ссылка, которую отдаём клиенту).

Коды ошибок: `400` — ошибка валидации, `401` — ошибка аутентификации.

**Альтернатива — `POST /v2/transaction/process`**: то же самое, но без
`paymentMethod`; способ оплаты выбирает сам плательщик на странице Platega.
Ответ другой по именам полей:

```json
{
  "transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "PENDING",
  "url": "https://pay.platega.io/?id=f8000067-...&mh=0a0000a4-...",
  "expiresIn": "00:15:00",
  "rate": 91.2
}
```

То есть ссылка здесь называется `url`, а не `redirect`. Код ниже обрабатывает оба варианта.

### 1.4. Проверка статуса платежа (поллинг / сверка)

**`GET https://app.platega.io/transaction/{id}`**, заголовки те же.

Ответ (`TransactionStatusResponse`) содержит, помимо прочего:
`id`, `status`, `paymentDetails.amount`, `paymentDetails.currency`, `description`,
`payload`, `externalId`, `comission`, `comissionUsdt`, `amountUsdt`, `qr`,
`payformSuccessUrl`, `expiresIn`, `merchantName`, `mechantId` (да, с опечаткой в доке),
`comissionType`, `return`.

Важно: **`payload` здесь тоже возвращается** — это наш страховочный механизм, если в
callback'е промокод не придёт.

### 1.5. Статусы

`PaymentStatus` (enum):

| Статус | Значение |
| --- | --- |
| `PENDING` | ожидание оплаты |
| `CONFIRMED` | **успешная оплата** — начисляем вознаграждение |
| `CANCELED` | неуспешная оплата / отмена — не начисляем |
| `CHARGEBACKED` | возврат средств — начисление надо откатить |

Успешность определяется строго значением `status == "CONFIRMED"`.

> Нюанс документации: в схеме `CallbackPayload` enum статуса урезан до
> `CONFIRMED | CANCELED`, но в описании вебхука явно сказано, что при возврате
> средств приходит `CHARGEBACKED`. Код обязан быть готов к обоим вариантам
> и не падать на неизвестном статусе.

### 1.6. Вебхук (callback об изменении статуса транзакции)

- **Метод:** `POST` на ваш URL.
- **Куда прописывается URL:** личный кабинет my.platega.io → **Настройки → Callback URLs**.
- **Заголовки от Platega:** `X-MerchantId` (ваш MerchantId, UUID) и `X-Secret` (ваш API-ключ).
- **Тело:** JSON.

| Поле | Тип | Обяз. | Описание |
| --- | --- | --- | --- |
| `id` | string (uuid) | да | ID транзакции — **ключ идемпотентности** |
| `amount` | number (float) | да | Сумма |
| `currency` | string | да | Валюта (`RUB`) |
| `status` | string | да | `CONFIRMED` / `CANCELED` (+ `CHARGEBACKED` при возврате) |
| `paymentMethod` | integer | нет | ID метода оплаты |
| `payload` | string | да* | Наша произвольная строка из запроса на создание платежа |

\* `payload` помечен как required в описании вебхука, но **отсутствует в схеме
`CallbackPayload`**. Считаем, что он может не прийти, и в этом случае дотягиваем
его через `GET /transaction/{id}`.

Пример тела:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "amount": 1000,
  "currency": "RUB",
  "status": "CONFIRMED",
  "paymentMethod": 2,
  "payload": "{\"promo\":\"NICKNAME\",\"uid\":123456789}"
}
```

**Что ждёт Platega от нашего сервера:** HTTP-ответ `200`. Тело ответа в документации
не специфицировано — отдаём `200 OK` с текстом `OK`, это безопасно.

**Ретраи:** если успешного ответа нет в течение **60 секунд**, запрос отменяется,
затем выполняется **до 3 повторных попыток с интервалом 5 минут**.
Значит, обработчик обязан быть идемпотентным и быстрым (тяжёлое — в фон).

**Требования к callback URL (жёсткие, из документации):**

- Только **HTTPS**, HTTP запрещён.
- Только публичные IP-адреса или доменные имена.
- Требуется корректный SSL-сертификат от доверенного УЦ.
- Самоподписанные сертификаты **не допускаются**.
- Запрещены приватные диапазоны `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`.
- Запрещены `localhost` и loopback.

### 1.7. Проверка подлинности вебхука — ВАЖНО

**В документации Platega НЕТ криптографической подписи вебхука.** Нет ни HMAC-SHA256,
ни SHA512, ни MD5, ни поля `sign`/`signature`, ни заголовка с подписью — ни на странице
вебхука, ни в схеме `CallbackPayload`.

Аутентификация вебхука реализована так: **Platega присылает обратно ваши собственные
`X-MerchantId` и `X-Secret` в заголовках**. То есть проверка сводится к сравнению
пришедших значений с вашими (константное по времени сравнение, `hmac.compare_digest`).

Практические следствия:

1. Секрет реально передаётся по сети в каждом callback'е → **HTTPS обязателен**, и
   `X-Secret` нельзя логировать.
2. Дополнительно стоит сделать URL вебхука «неугадываемым»: не `/platega/callback`,
   а, например, `/platega/cb/<длинная_случайная_строка>`.
3. Желательно ограничить доступ по IP Platega на уровне nginx — **список IP надо
   запросить у поддержки** (см. «Открытые вопросы»).
4. HMAC-подпись `PG-HMAC` (`Authorization: PG-HMAC kid=..., ts=..., sig=...`,
   HMAC-SHA256 от `METHOD\nPATH\nts\nidem_key\nsha256_hex(body)`, Base64) относится
   **только к Payout API на `api.platega.io`** и к callback'ам отношения не имеет.
   Не пытайтесь применить её к вебхуку.

### 1.8. Идемпотентность

Ключ — **`id` транзакции (UUID)** из тела вебхука. Он же `transactionId` из ответа на
создание платежа. Храним в таблице `payments` с `UNIQUE`-ограничением и отсекаем
повторы (до 3 ретраев от Platega + возможные дубли при смене статуса).

### 1.9. Суммы и валюта

- `amount` — **число** (`number`, `format: float`), **НЕ строка**.
- **В рублях, не в копейках.** В примерах документации `amount: 500`, `amount: 1000`,
  `amount: 136.12`, `amount: 1.15` — дробные значения допустимы, значит это основная
  единица валюты.
- `currency` — строка, `"RUB"`.
- В выгрузке транзакций поле называется `currencyCode`, а сумма — `amount`.

---

## 2. План реализации в боте

### 2.1. Архитектура

```
                   ┌────────────────────────┐
  клиент платит    │   Platega              │
  ───────────────► │   app.platega.io       │
                   └───────────┬────────────┘
                               │ POST callback (HTTPS)
                               ▼
                     ┌───────────────────┐
                     │ nginx (443, TLS)  │
                     └─────────┬─────────┘
                               │ proxy_pass → 127.0.0.1:8081
                               ▼
   ┌───────────────────────────────────────────────────┐
   │  Один процесс Python                              │
   │  ├─ aiogram Dispatcher.start_polling(bot)         │
   │  └─ aiohttp AppRunner  (bot/webhook.py)           │
   │         └─ POST /platega/callback                 │
   │              → проверка X-MerchantId/X-Secret     │
   │              → дедупликация по payments.transaction_id
   │              → INSERT INTO sales                  │
   │              → bot.send_message(owner_id, ...)    │
   └───────────────────────────────────────────────────┘
                               │
                               ▼
                        SQLite (aiosqlite)
```

### 2.2. DDL таблицы `payments`

```sql
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT    NOT NULL UNIQUE,   -- Platega transaction id (UUID)
    promo_code      TEXT,                      -- промокод как пришёл в payload
    promo_id        INTEGER,                   -- FK -> promos.id (проставляется после матчинга)
    sale_id         INTEGER,                   -- FK -> sales.id (NULL = начисления ещё не было)
    amount          REAL    NOT NULL DEFAULT 0,
    currency        TEXT    NOT NULL DEFAULT 'RUB',
    status          TEXT    NOT NULL,          -- PENDING / CONFIRMED / CANCELED / CHARGEBACKED
    payment_method  INTEGER,
    payload         TEXT,                      -- сырой payload для разбора инцидентов
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_payments_promo_id ON payments(promo_id);
CREATE INDEX IF NOT EXISTS idx_payments_status   ON payments(status);
```

`UNIQUE(transaction_id)` — это и есть механизм идемпотентности. `sale_id IS NOT NULL`
означает «по этой транзакции вознаграждение уже начислено, второй раз не начисляем».

### 2.3. Модуль `bot/platega.py`

```python
"""Клиент Platega API + утилиты для callback'ов.

Документация: https://docs.platega.io/
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

log = logging.getLogger(__name__)

PLATEGA_BASE_URL = "https://app.platega.io"

# Способы оплаты (PaymentMethodInt из документации)
PM_SBP_QR = 2
PM_ERIP = 3
PM_CARD = 11
PM_INTERNATIONAL = 12
PM_CRYPTO = 13
PM_SBERPAY = 14

# Статусы транзакции (PaymentStatus из документации)
STATUS_PENDING = "PENDING"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELED = "CANCELED"
STATUS_CHARGEBACKED = "CHARGEBACKED"

#: Статусы, при которых покупка считается состоявшейся.
SUCCESS_STATUSES = frozenset({STATUS_CONFIRMED})
#: Статусы, при которых начисление делать нельзя.
FAILED_STATUSES = frozenset({STATUS_CANCELED, STATUS_CHARGEBACKED})


class PlategaError(RuntimeError):
    """Ошибка обращения к Platega API."""

    def __init__(self, message: str, status: Optional[int] = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class PlategaPayment:
    """Нормализованный ответ на создание транзакции."""

    transaction_id: str
    status: str
    pay_url: str
    expires_in: Optional[str] = None
    raw: dict = field(default_factory=dict)


def build_payload(
    promo_code: str,
    tg_user_id: Optional[int] = None,
    order_id: Optional[str] = None,
) -> str:
    """Собирает строку `payload` — единственное произвольное поле, которое Platega
    возвращает обратно в callback'е."""
    data: dict[str, Any] = {"promo": promo_code.strip().upper()}
    if tg_user_id is not None:
        data["uid"] = int(tg_user_id)
    if order_id:
        data["order"] = str(order_id)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def parse_payload(payload: Optional[str]) -> dict[str, Any]:
    """Разбирает `payload` из callback'а.

    Поддерживает три формата, потому что строку может сформировать не только бот,
    но и сайт t-optim.cc: JSON, `promo=CODE;uid=123` и просто голый промокод.
    """
    if not payload:
        return {}
    text = str(payload).strip()
    if not text:
        return {}

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data

    if "=" in text:
        parsed: dict[str, Any] = {}
        for chunk in text.replace("&", ";").replace(",", ";").split(";"):
            if "=" not in chunk:
                continue
            key, _, value = chunk.partition("=")
            key = key.strip()
            if key:
                parsed[key] = value.strip()
        if parsed:
            return parsed

    return {"promo": text}


def extract_promo_code(payload: Optional[str]) -> Optional[str]:
    """Достаёт промокод из payload или возвращает None."""
    data = parse_payload(payload)
    for key in ("promo", "promo_code", "promoCode", "code"):
        value = data.get(key)
        if value:
            code = str(value).strip().upper()
            if code:
                return code
    return None


def verify_callback_auth(headers: Any, merchant_id: str, secret: str) -> bool:
    """Проверяет подлинность callback'а.

    Platega НЕ подписывает callback HMAC-ом. Подлинность подтверждается тем,
    что в заголовках приходят наши собственные `X-MerchantId` и `X-Secret`.
    Поэтому эндпоинт обязан быть доступен только по HTTPS.
    """
    got_merchant = (headers.get("X-MerchantId") or "").strip()
    got_secret = (headers.get("X-Secret") or "").strip()
    if not got_merchant or not got_secret:
        return False
    # Сравниваем байты: compare_digest на str падает с TypeError при не-ASCII.
    return hmac.compare_digest(
        got_merchant.encode("utf-8"), merchant_id.strip().encode("utf-8")
    ) and hmac.compare_digest(got_secret.encode("utf-8"), secret.strip().encode("utf-8"))


class PlategaClient:
    """Минимальный асинхронный клиент Platega (создание платежа + статус)."""

    def __init__(
        self,
        merchant_id: str,
        secret: str,
        base_url: str = PLATEGA_BASE_URL,
        timeout: int = 30,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        if not merchant_id or not secret:
            raise ValueError("PLATEGA_MERCHANT_ID и PLATEGA_SECRET обязательны")
        self.merchant_id = merchant_id
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.request(
            method, url, headers=self._headers, json=payload, timeout=self._timeout
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise PlategaError(
                    f"Platega {method} {path} -> HTTP {resp.status}", resp.status, text
                )
            if not text:
                return {}
            try:
                return json.loads(text)
            except ValueError as exc:
                raise PlategaError(f"Невалидный JSON от Platega: {text[:300]}") from exc

    async def create_payment(
        self,
        amount: float,
        promo_code: str,
        description: str,
        return_url: str,
        failed_url: str,
        tg_user_id: Optional[int] = None,
        tg_username: Optional[str] = None,
        order_id: Optional[str] = None,
        currency: str = "RUB",
        payment_method: Optional[int] = PM_SBP_QR,
    ) -> PlategaPayment:
        """Создаёт транзакцию и возвращает ссылку на оплату.

        Промокод уходит в `payload` — это поле возвращается в callback'е.
        Если `payment_method is None`, используется /v2/transaction/process,
        где способ оплаты выбирает сам плательщик.
        """
        body: dict[str, Any] = {
            "paymentDetails": {"amount": float(amount), "currency": currency},
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": build_payload(promo_code, tg_user_id=tg_user_id, order_id=order_id),
        }
        if order_id:
            body["orderId"] = str(order_id)
        if tg_user_id is not None:
            # metadata.userId нужен антифроду; userName — любые доп. данные.
            body["metadata"] = {
                "userId": str(tg_user_id),
                "userName": tg_username or f"id{tg_user_id}",
            }

        if payment_method is None:
            path = "/v2/transaction/process"
        else:
            path = "/transaction/process"
            body["paymentMethod"] = int(payment_method)

        data = await self._request("POST", path, body)
        if not isinstance(data, dict):
            raise PlategaError(f"Неожиданный ответ Platega: {data!r}")

        transaction_id = data.get("transactionId") or data.get("id") or ""
        pay_url = data.get("redirect") or data.get("url") or ""
        if not transaction_id or not pay_url:
            raise PlategaError(f"В ответе Platega нет transactionId/ссылки: {data!r}")

        return PlategaPayment(
            transaction_id=str(transaction_id),
            status=str(data.get("status") or STATUS_PENDING),
            pay_url=str(pay_url),
            expires_in=data.get("expiresIn"),
            raw=data,
        )

    async def get_transaction(self, transaction_id: str) -> dict:
        """GET /transaction/{id} — фолбэк, если в callback'е не пришёл payload,
        и инструмент ручной сверки."""
        data = await self._request("GET", f"/transaction/{transaction_id}")
        return data if isinstance(data, dict) else {}
```

### 2.4. Модуль `bot/webhook.py`

```python
"""aiohttp-сервер для приёма callback'ов Platega.

Запускается в том же процессе, что и polling-бот aiogram.
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from typing import Optional

import aiosqlite
from aiohttp import web
from aiogram import Bot

from .platega import (
    FAILED_STATUSES,
    SUCCESS_STATUSES,
    PlategaClient,
    PlategaError,
    extract_promo_code,
    verify_callback_auth,
)

log = logging.getLogger(__name__)

PAYMENTS_DDL = """
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT    NOT NULL UNIQUE,
    promo_code      TEXT,
    promo_id        INTEGER,
    sale_id         INTEGER,
    amount          REAL    NOT NULL DEFAULT 0,
    currency        TEXT    NOT NULL DEFAULT 'RUB',
    status          TEXT    NOT NULL,
    payment_method  INTEGER,
    payload         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_promo_id ON payments(promo_id);
CREATE INDEX IF NOT EXISTS idx_payments_status  ON payments(status);
"""


@dataclass
class WebhookConfig:
    merchant_id: str
    secret: str
    db_path: str
    admin_id: Optional[int] = None
    path: str = "/platega/callback"
    host: str = "127.0.0.1"
    port: int = 8081


async def init_payments_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(PAYMENTS_DDL)
        await db.commit()


async def _claim_payment(
    db: aiosqlite.Connection,
    transaction_id: str,
    status: str,
    amount: float,
    currency: str,
    payment_method: Optional[int],
    payload: Optional[str],
    promo_code: Optional[str],
) -> bool:
    """Идемпотентность: True — этот callback надо обработать, False — дубль."""
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO payments
            (transaction_id, promo_code, amount, currency, status, payment_method, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (transaction_id, promo_code, amount, currency, status, payment_method, payload),
    )
    await db.commit()
    if cursor.rowcount == 1:
        return True

    cursor = await db.execute(
        "SELECT status, sale_id FROM payments WHERE transaction_id = ?",
        (transaction_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    if row["sale_id"] is not None:
        log.info("Platega callback %s: начисление уже сделано, пропускаем", transaction_id)
        return False
    if (row["status"] or "").upper() == status:
        log.info("Platega callback %s: повтор того же статуса %s", transaction_id, status)
        return False

    await db.execute(
        """
        UPDATE payments
           SET status = ?, amount = ?, currency = ?, payment_method = ?,
               payload = COALESCE(?, payload), promo_code = COALESCE(?, promo_code),
               updated_at = datetime('now')
         WHERE transaction_id = ?
        """,
        (status, amount, currency, payment_method, payload, promo_code, transaction_id),
    )
    await db.commit()
    return True


async def _find_promo(db: aiosqlite.Connection, promo_code: str):
    cursor = await db.execute(
        "SELECT id, code, reward, owner_id FROM promos WHERE code = ? COLLATE NOCASE",
        (promo_code,),
    )
    return await cursor.fetchone()


async def _notify_owner(
    bot: Bot,
    owner_id: int,
    promo_code: str,
    amount: float,
    reward: float,
    transaction_id: str,
) -> None:
    text = (
        "\U0001f514 <b>Новая покупка по твоему промокоду!</b>\n\n"
        f"Промокод: <code>{html.escape(promo_code)}</code>\n"
        f"Сумма заказа: <b>{amount:.0f} \u20bd</b>\n"
        f"Твоё вознаграждение: <b>+{reward:.0f} \u20bd</b>\n\n"
        f"<i>Платёж {html.escape(transaction_id[:8])}…</i>"
    )
    try:
        await bot.send_message(owner_id, text, parse_mode="HTML")
    except Exception:
        log.exception("Не удалось отправить уведомление партнёру %s", owner_id)


async def _notify_admin(bot: Bot, admin_id: Optional[int], text: str) -> None:
    if not admin_id:
        return
    try:
        await bot.send_message(admin_id, text, parse_mode="HTML")
    except Exception:
        log.exception("Не удалось отправить уведомление админу %s", admin_id)


async def process_callback(
    bot: Bot,
    cfg: WebhookConfig,
    data: dict,
    client: Optional[PlategaClient] = None,
) -> None:
    transaction_id = str(data.get("id") or "").strip()
    status = str(data.get("status") or "").strip().upper()
    currency = str(data.get("currency") or "RUB")
    payload = data.get("payload")
    payment_method = data.get("paymentMethod")
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0

    promo_code = extract_promo_code(payload if isinstance(payload, str) else None)
    if promo_code is None and client is not None:
        # Фолбэк: в callback'е payload не пришёл — добираем его из GET /transaction/{id}.
        try:
            transaction = await client.get_transaction(transaction_id)
            promo_code = extract_promo_code(transaction.get("payload"))
        except PlategaError:
            log.warning("Не удалось получить транзакцию %s из Platega", transaction_id)

    notify: Optional[tuple] = None
    admin_text: Optional[str] = None

    async with aiosqlite.connect(cfg.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")

        claimed = await _claim_payment(
            db, transaction_id, status, amount, currency, payment_method, payload, promo_code
        )
        if not claimed:
            return

        if status in FAILED_STATUSES:
            log.info("Платёж %s в статусе %s — начисление не делаем", transaction_id, status)
            return
        if status not in SUCCESS_STATUSES:
            log.info("Платёж %s в промежуточном статусе %s", transaction_id, status)
            return

        if not promo_code:
            log.info("Платёж %s подтверждён, но промокода нет — обычная продажа", transaction_id)
            return

        promo = await _find_promo(db, promo_code)
        if promo is None:
            log.warning("Платёж %s: промокод %s не найден в БД", transaction_id, promo_code)
            admin_text = (
                "\u26a0\ufe0f Оплата прошла, но промокод "
                f"<code>{html.escape(promo_code)}</code> не найден.\n"
                f"Транзакция: <code>{html.escape(transaction_id)}</code>"
            )
        else:
            reward = float(promo["reward"] or 0)
            cursor = await db.execute(
                """
                INSERT INTO sales (promo_id, amount, reward, note, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (promo["id"], amount, reward, f"Platega {transaction_id}"),
            )
            sale_id = cursor.lastrowid
            await db.execute(
                """
                UPDATE payments
                   SET promo_id = ?, sale_id = ?, promo_code = ?, updated_at = datetime('now')
                 WHERE transaction_id = ?
                """,
                (promo["id"], sale_id, promo["code"], transaction_id),
            )
            await db.commit()
            notify = (promo["owner_id"], promo["code"], amount, reward)

    if notify is not None:
        owner_id, code, sale_amount, reward = notify
        if owner_id:
            await _notify_owner(bot, int(owner_id), str(code), sale_amount, reward, transaction_id)
    if admin_text:
        await _notify_admin(bot, cfg.admin_id, admin_text)


async def handle_callback(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    cfg: WebhookConfig = request.app["platega_cfg"]
    client: Optional[PlategaClient] = request.app.get("platega_client")

    raw = await request.read()

    if not verify_callback_auth(request.headers, cfg.merchant_id, cfg.secret):
        log.warning("Platega callback: неверные X-MerchantId/X-Secret от %s", request.remote)
        return web.Response(status=401, text="unauthorized")

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log.warning("Platega callback: невалидный JSON: %r", raw[:300])
        return web.Response(status=400, text="bad request")

    if not isinstance(data, dict) or not str(data.get("id") or "").strip():
        return web.Response(status=400, text="bad request")

    log.info("Platega callback: %s", data)

    try:
        await process_callback(bot, cfg, data, client=client)
    except Exception:
        # 5xx -> Platega повторит попытку (до 3 раз с интервалом 5 минут).
        log.exception("Ошибка обработки callback Platega")
        return web.Response(status=500, text="error")

    return web.Response(status=200, text="OK")


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def create_app(
    bot: Bot,
    cfg: WebhookConfig,
    client: Optional[PlategaClient] = None,
) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["platega_cfg"] = cfg
    app["platega_client"] = client
    app.router.add_post(cfg.path, handle_callback)
    app.router.add_get("/healthz", handle_health)
    return app


async def start_webhook_server(
    bot: Bot,
    cfg: WebhookConfig,
    client: Optional[PlategaClient] = None,
) -> web.AppRunner:
    await init_payments_table(cfg.db_path)
    app = create_app(bot, cfg, client=client)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.host, port=cfg.port)
    await site.start()
    log.info("Platega webhook слушает http://%s:%s%s", cfg.host, cfg.port, cfg.path)
    return runner
```

### 2.5. Запуск одним процессом вместе с polling-ботом

Веб-сервер поднимается через `AppRunner` (он не блокирует), после чего в том же
event loop стартует polling. Отдельный `asyncio.gather` не нужен, но вариант с ним
тоже приведён.

```python
# bot/main.py (фрагмент — встроить в существующий main)
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from .platega import PlategaClient
from .webhook import WebhookConfig, start_webhook_server


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher()
    # ... router'ы бота ...

    db_path = os.getenv("DB_PATH", "bot.db")
    merchant_id = os.getenv("PLATEGA_MERCHANT_ID", "")
    secret = os.getenv("PLATEGA_SECRET", "")

    runner = None
    client = None

    if merchant_id and secret:
        client = PlategaClient(
            merchant_id,
            secret,
            base_url=os.getenv("PLATEGA_BASE_URL", "https://app.platega.io"),
        )
        cfg = WebhookConfig(
            merchant_id=merchant_id,
            secret=secret,
            db_path=db_path,
            admin_id=int(os.getenv("ADMIN_ID") or 0) or None,
            path=os.getenv("WEBHOOK_PATH", "/platega/callback"),
            host=os.getenv("WEBHOOK_HOST", "127.0.0.1"),
            port=int(os.getenv("WEBHOOK_PORT", "8081")),
        )
        runner = await start_webhook_server(bot, cfg, client=client)
    else:
        logging.warning("PLATEGA_* не заданы — вебхук не запущен, продажи только вручную")

    try:
        await dp.start_polling(bot)
    finally:
        if runner is not None:
            await runner.cleanup()
        if client is not None:
            await client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Вариант с `asyncio.gather`, если хочется явной параллельности:

```python
async def _serve_forever(runner):
    while True:
        await asyncio.sleep(3600)

await asyncio.gather(
    dp.start_polling(bot),
    _serve_forever(runner),
)
```

**Порт:** `8081` на `127.0.0.1` — наружу торчит только nginx. Слушать `0.0.0.0`
не нужно и небезопасно, т.к. секрет передаётся в заголовке по открытому HTTP
внутри сервера.

### 2.6. HTTPS: nginx перед ботом

Platega требует валидный сертификат доверенного УЦ, поэтому TLS терминируем на nginx
и получаем сертификат через Let's Encrypt (`certbot --nginx -d bot.t-optim.cc`).

```nginx
server {
    listen 80;
    server_name bot.t-optim.cc;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name bot.t-optim.cc;

    ssl_certificate     /etc/letsencrypt/live/bot.t-optim.cc/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.t-optim.cc/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # Логи без заголовков: в X-Secret лежит боевой ключ.
    access_log /var/log/nginx/platega-access.log;

    location /platega/callback {
        # Опционально: ограничение по IP Platega (список запросить у поддержки)
        # allow 1.2.3.4;
        # deny all;

        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Platega ждёт ответ до 60 секунд
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    location /healthz {
        proxy_pass http://127.0.0.1:8081;
    }

    location / {
        return 404;
    }
}
```

Проверка после настройки:

```bash
curl -i https://bot.t-optim.cc/healthz          # должен вернуть 200 ok
curl -i -X POST https://bot.t-optim.cc/platega/callback \
     -H 'Content-Type: application/json' -d '{}' # должен вернуть 401
```

Затем в my.platega.io → **Настройки → Callback URLs** указать
`https://bot.t-optim.cc/platega/callback`.

### 2.7. Переменные окружения (`.env.example`)

```dotenv
# --- Telegram ---
BOT_TOKEN=
ADMIN_ID=
DB_PATH=bot.db

# --- Platega ---
# Имена ровно как в документации: X-MerchantId / X-Secret
PLATEGA_MERCHANT_ID=
PLATEGA_SECRET=
PLATEGA_BASE_URL=https://app.platega.io
# Способ оплаты по умолчанию: 2=СБП QR, 11=карты, 14=SberPay.
# Пусто -> используется /v2/transaction/process (выбор метода плательщиком)
PLATEGA_PAYMENT_METHOD=2
PLATEGA_RETURN_URL=https://t-optim.cc/pay/success
PLATEGA_FAILED_URL=https://t-optim.cc/pay/fail
# Цена услуги по умолчанию, в рублях
PLATEGA_DEFAULT_AMOUNT=2500

# --- Локальный aiohttp-сервер для callback'ов ---
WEBHOOK_HOST=127.0.0.1
WEBHOOK_PORT=8081
WEBHOOK_PATH=/platega/callback
# Публичный HTTPS-адрес (его прописываем в ЛК Platega). Только для справки/логов.
WEBHOOK_PUBLIC_URL=https://bot.t-optim.cc/platega/callback
```

**Отдельного `PLATEGA_WEBHOOK_SECRET` не существует.** Callback аутентифицируется тем же
`PLATEGA_SECRET`, который Platega присылает обратно в заголовке `X-Secret`. Вводить
лишнюю переменную не нужно — это создало бы ложное ощущение, что есть отдельный
секрет подписи.

---

## 3. Сценарий А (основной): промокод передаёт сайт t-optim.cc

Сайт при создании платежа кладёт промокод в `payload`:

```json
{ "payload": "{\"promo\":\"NICKNAME\",\"order\":\"to-10231\"}" }
```

Бот ничего не создаёт, только принимает callback. Это единственное, что надо
попросить у разработчика сайта: **добавить поле `payload` в тело запроса
`POST /transaction/process`**. Формат payload — на выбор, `parse_payload()` понимает
и JSON, и `promo=NICKNAME`, и голый промокод строкой.

Если на сайте промокод вводится в отдельное поле формы — достаточно прокинуть его
в `payload` как есть.

## 4. Сценарий Б (fallback): бот сам создаёт платёжную ссылку

Применяем, если на сайте нельзя изменить код и прокинуть промокод в платёж.
Тогда точка входа клиента — бот: клиент вводит промокод, бот выдаёт ссылку на оплату,
и промокод гарантированно попадает в `payload`.

```python
# bot/handlers/payment.py (эскиз)
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..platega import PlategaError

router = Router()


class PayStates(StatesGroup):
    waiting_promo = State()


@router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext) -> None:
    await state.set_state(PayStates.waiting_promo)
    await message.answer(
        "Введите промокод партнёра.\n"
        "Если промокода нет — отправьте «-»."
    )


@router.message(PayStates.waiting_promo, F.text)
async def process_promo(message: Message, state: FSMContext, platega) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    promo_code = "" if raw in {"-", "—"} else raw.upper()

    amount = float(os.getenv("PLATEGA_DEFAULT_AMOUNT", "2500"))
    method_env = os.getenv("PLATEGA_PAYMENT_METHOD", "").strip()

    try:
        payment = await platega.create_payment(
            amount=amount,
            promo_code=promo_code,
            description=f"Настройка ПК t-optim.cc для {message.from_user.id}",
            return_url=os.environ["PLATEGA_RETURN_URL"],
            failed_url=os.environ["PLATEGA_FAILED_URL"],
            tg_user_id=message.from_user.id,
            tg_username=f"@{message.from_user.username}" if message.from_user.username else None,
            payment_method=int(method_env) if method_env else None,
        )
    except PlategaError:
        await message.answer("Не удалось создать платёж. Попробуйте позже или напишите в поддержку.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=payment.pay_url)]]
    )
    await message.answer(
        f"Счёт на {amount:.0f} ₽ создан.\n"
        f"Ссылка действительна {payment.expires_in or '15 минут'}.",
        reply_markup=keyboard,
    )
```

`PlategaClient` удобно прокинуть в хендлеры через `dp["platega"] = client` или
`dp.workflow_data.update(platega=client)` — тогда aiogram сам подставит аргумент
`platega` в сигнатуру хендлера.

Важно: ссылка живёт **15 минут** (`expiresIn: "00:15:00"`). Если клиент не успел —
нужна кнопка «создать новую ссылку».

## 5. Порядок внедрения

1. Получить у менеджера Platega `MerchantId` и `Secret`, положить в `.env`.
2. Поднять домен `bot.t-optim.cc`, получить сертификат Let's Encrypt, настроить nginx.
3. Применить DDL таблицы `payments` (миграция вызывается автоматически из
   `init_payments_table()` при старте).
4. Прописать callback URL в my.platega.io → Настройки → Callback URLs.
5. Провести тестовый платёж на минимальную сумму с реальным промокодом и убедиться,
   что пришёл callback, создалась запись в `sales` и партнёр получил уведомление.
6. Повторно «проиграть» тот же callback вручную (`curl` с теми же заголовками и телом)
   и убедиться, что второго начисления не произошло.

---

## 6. Открытые вопросы

Эти пункты **отсутствуют в публичной документации** — их нужно уточнить у поддержки
Platega или посмотреть в личном кабинете. Ничего из этого не выдумано в коде выше:
там, где информации нет, сделано безопасное поведение по умолчанию.

### К поддержке / менеджеру Platega

1. **Тестовый режим / песочница.** В документации нет ни слова о sandbox, тестовых
   мерчантах или тестовых картах. Есть ли тестовый контур, или первый платёж
   придётся делать боевым на минимальную сумму? Какова минимальная сумма платежа?
2. **Список IP-адресов, с которых приходят callback'и** — нужен для `allow/deny` в
   nginx. Это единственный способ усилить защиту вебхука при отсутствии подписи.
3. **Действительно ли нет подписи вебхука?** Подтвердить, что аутентификация callback'а
   сводится к `X-MerchantId` + `X-Secret` в заголовках, и нет опционального режима
   с HMAC-подписью. Если есть — запросить алгоритм и имя заголовка.
4. **Приходит ли `payload` в callback'е всегда?** В описании вебхука он `required`,
   но в схеме `CallbackPayload` его нет, а в примерах `confirmed`/`canceled` он не
   показан. Это ключевой вопрос: именно через `payload` мы передаём промокод.
5. **Возвращаются ли в callback'е `orderId` и `metadata`?** В схеме их нет. Если бы
   возвращались — промокод можно было бы класть в `orderId`, что надёжнее.
6. **Длина поля `payload`** — максимальное число символов? Нужно, чтобы понимать,
   влезет ли JSON вида `{"promo":"...","uid":...,"order":"..."}`.
7. **Статус `CHARGEBACKED`:** приходит ли он отдельным callback'ом по той же
   транзакции с тем же `id`? Нужно для корректного отката начисления партнёру.
8. **Требуется ли `metadata` для нашей категории магазина?** Напрямую влияет на
   антифрод и риск отключения магазина.
9. **Можно ли задать время жизни ссылки больше 15 минут?**
10. **Можно ли указать несколько Callback URL** (например, отдельный для сайта и
    отдельный для бота), или URL один на мерчанта? Это влияет на архитектуру: если
    URL один, а сайт уже использует свой — callback придётся принимать в одном месте
    и роутить дальше.

### К пользователю (владельцу t-optim.cc)

1. **Есть ли доступ к коду сайта t-optim.cc?** От этого зависит выбор между
   сценарием А (сайт кладёт промокод в `payload`) и сценарием Б (бот создаёт ссылку).
2. **Уже настроен ли Callback URL в my.platega.io под нужды сайта?** Если да — см.
   вопрос 10 выше, возможен конфликт.
3. **Есть ли домен и сервер с публичным IP для бота?** Без HTTPS-домена с валидным
   сертификатом вебхук Platega работать не будет в принципе.
4. **Фиксированная цена услуги или их несколько?** В коде зашита одна
   `PLATEGA_DEFAULT_AMOUNT`; если тарифов несколько — нужен выбор тарифа в боте.
5. **Какой способ оплаты предпочтителен** — СБП (`2`), карты (`11`), SberPay (`14`),
   или дать выбор плательщику (`/v2/transaction/process`)?
6. **Что делать при возврате (`CHARGEBACKED`)** — автоматически списывать начисление
   у партнёра или только уведомлять админа?
7. **Должно ли вознаграждение зависеть от суммы заказа?** Сейчас берётся фиксированный
   `promos.reward` независимо от `amount` — это соответствует текущей логике бота.

---

## 7. Статус проверки кода

- Код выше вычитан на синтаксическую корректность вручную. Запустить
  `python -m py_compile` не удалось: в рабочей сессии не отвечал терминал.
  **Перед интеграцией прогоните `python -m py_compile bot/platega.py bot/webhook.py`.**
- Код намеренно не привязан к модулю `db.py` соседнего агента: он открывает
  соединения через `aiosqlite.connect(db_path)` напрямую. При интеграции
  замените эти вызовы на существующие в проекте хелперы, если они есть, и сверьте
  фактические имена колонок в `promos` и `sales` (в коде использованы
  `promos(id, code, reward, owner_id)` и `sales(promo_id, amount, reward, note, created_at)`
  из описания задачи).
- `_claim_payment()` опирается на `cursor.rowcount == 1` после `INSERT OR IGNORE`.
  Если поведение драйвера окажется иным, эквивалентная проверка —
  `SELECT changes()` сразу после вставки.
