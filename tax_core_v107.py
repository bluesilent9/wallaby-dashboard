#!/usr/bin/env python3
"""
Bitcoin CGT Generator v107 - Australian tax working paper

Inputs:
  * Bitaroo AUD Statement PDF
  * Bitaroo BTC Statement PDF
  * one or more Bitcoin Core wallet descriptor-derived records (each wallet name is supplied by the user)
  * Historical Wallet ODS file with one wallet per tab and UTC date/time columns
  * Other Exchanges ODS spreadsheet containing signed fiat and BTC trade legs

The program traces BTC lots through Bitaroo and the wallets, using pro-rata
allocation across all lots (the same broad approach used in the supplied
manual working paper). It loads every usable Bitaroo AUD/BTC Financial Year statement found in the
user-selected directory before the tax trace begins. The target FY may be a
partial/YTD statement; older FYs must be complete 1-Jul to 30-Jun statements.
There is no hard-coded historical start date or historical floor. Other Exchanges ODS rows use Date (UTC), Time (UTC), sold-unit, Network Fee, received-unit headings, exactly five columns. Outgoing amounts and fees are nonpositive; the received amount is positive. Network fees use the outgoing crypto unit (incoming BTC for fiat purchases). Exactly one leg must be BTC. Asset units are read from the first-row column headings, never from cell currency formatting. Non-AUD fiat is translated at the transaction timestamp where supplied, otherwise using a historical daily close. Exchange-tab ownership is decided once for each tab after the Other Exchanges spreadsheet is loaded; tabs belonging to somebody else are discarded for that run.

Important: this is a working-paper generator for review by a registered
Australian tax agent/accountant. It is not tax advice and it does not lodge a
return.

Dependencies (Ubuntu system-wide):
    sudo apt update
    sudo apt install python3-pypdf python3-odf

PyPI alternative:
    python3 -m pip install pypdf odfpy

Output in the current directory:
  <taxpayer>_Bitcoin_CGT_<FY>.txt

All tracing, source-lot history, formulas, gains/losses and totals are written to the single text working paper.
"""
from __future__ import annotations

import re
import os
import argparse
import builtins
import hashlib
import sqlite3
import sys
import csv
import io
import zipfile
import bisect
import json
import urllib.error
import urllib.parse
import urllib.request
import time
import html as html_lib
from html.parser import HTMLParser
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from pathlib import Path
from functools import lru_cache
from zoneinfo import ZoneInfo
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from pypdf import PdfReader
except ImportError as exc:
    raise SystemExit("Missing dependency: pypdf. Install with: pip install pypdf odfpy") from exc

try:
    from odf.opendocument import load as load_ods
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
except ImportError as exc:
    raise SystemExit("Missing dependency: odfpy. On Ubuntu, install it with: sudo apt install python3-odf") from exc

# ---------------------------------------------------------------------------
# Tax/config constants
# ---------------------------------------------------------------------------

REFORM_DATE = date(2027, 7, 1)
REFORM_EVE = date(2027, 6, 30)
MATCH_HOURS = 168
UTC = timezone.utc
LOCAL_ZONE = None  # None uses the operating system timezone, including historical DST.
DATE_ONLY_NOTES = set()
NETWORK_SECONDS = 0.0
NETWORK_CALLS = 0
CACHE_HITS = 0
PROMPT_SECONDS = 0.0
THROTTLE_SECONDS = 0.0
LAST_KRAKEN_REQUEST = 0.0
# Reuse valid cached responses from the previous releases.
CACHE_PATH = Path(".bitcoin_cgt_v105_cache.sqlite3")
HTTP_TIMEOUT = 12
QUOTE_MAX_AGE_SECONDS = 3600
RUN_STARTED = time.perf_counter()
BTC_EPS = Decimal("0.000000000001")
# Bitaroo statements commonly show a residual 0.00000001 BTC after a complete
# sell/withdrawal. Treat that dust balance as an exhausted holding episode when
# determining historical dependencies. It is not economically meaningful for the
# target-year cost-basis trace and, importantly, prevents an old fully-exhausted
# transaction from pulling unrelated prior history into the dependency graph.
BITAROO_DUST_BALANCE_EPS = Decimal("0.00000002")
MONEY_EPS = Decimal("0.005")

# The supplied manual working paper treated Bitaroo "Sell Fee" lines as a
# separate exchange fee but did not subtract them from sale proceeds. Keep the
# default consistent with that file. Set True if the accountant wants those
# fiat-paid disposal fees treated as incidental disposal costs.
INCLUDE_FIAT_DISPOSAL_FEES = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def prompt_input(prompt):
    global PROMPT_SECONDS
    started = time.perf_counter()
    try:
        return builtins.input(prompt)
    finally:
        PROMPT_SECONDS += time.perf_counter() - started


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def qmoney(x: Decimal) -> Decimal:
    return D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def qb(x: Decimal) -> str:
    # Always render BTC quantities in fixed-point notation.  Decimal
    # quantization of zero otherwise displays as 0E-8, which is technically
    # zero but unnecessarily confusing in the working paper.
    return format(D(x).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f")


def human_date(x: date) -> str:
    return x.strftime("%d%b%y").upper()


def parse_money(s: str) -> Decimal:
    return Decimal(s.replace(",", "").replace("$", "").strip())


def parse_fy(text: str) -> int:
    raw = text.strip().replace("/", "-").replace("–", "-").replace(" ", "")
    m = re.fullmatch(r"(20\d{2})-(\d{2}|20\d{2})", raw)
    if not m:
        m = re.fullmatch(r"(\d{2})-(\d{2})", raw)
    if not m:
        raise ValueError("Financial year must look like 2025-26 or 25-26")
    a = int(m.group(1))
    b = int(m.group(2))
    if a < 100:
        a += 2000
    if b < 100:
        b += 2000 if b >= a % 100 else 1900
    if b != a + 1:
        raise ValueError("Financial year must span one year, e.g. 2025-26")
    return a


def fmt_fy(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"


def fy_bounds(start: int) -> Tuple[date, date]:
    return date(start, 7, 1), date(start + 1, 6, 30)


class TransactionDate(date):
    """Tax calendar date with the original UTC instant retained.

    Date arithmetic/comparisons retain the V93 ledger interface. Valuation and
    matching MUST use utc_time(), never date equality as a quote cache key.
    """
    def __new__(cls, instant: datetime):
        if instant.tzinfo is None:
            raise ValueError("TransactionDate requires an aware datetime")
        instant = instant.astimezone(UTC)
        local = instant.astimezone(LOCAL_ZONE)
        obj = super().__new__(cls, local.year, local.month, local.day)
        obj.utc = instant
        return obj

    def __copy__(self):
        return type(self)(self.utc)

    def __deepcopy__(self, memo):
        return type(self)(self.utc)


def local_to_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    possible = []
    for fold in (0, 1):
        if LOCAL_ZONE is None:
            candidate = value.replace(fold=fold).astimezone(UTC)
        else:
            candidate = value.replace(tzinfo=LOCAL_ZONE, fold=fold).astimezone(UTC)
        if candidate.astimezone(LOCAL_ZONE).replace(tzinfo=None) == value:
            if candidate not in possible:
                possible.append(candidate)
    if len(possible) != 1:
        raise ValueError(f"Ambiguous/nonexistent local time {value}: supply an explicit UTC offset")
    return possible[0]


def tax_date(value: datetime) -> TransactionDate:
    # Bitaroo statement timestamps are documented as UTC.
    return TransactionDate(value.replace(tzinfo=UTC) if value.tzinfo is None else value)


def utc_time(value, require=False) -> datetime:
    if isinstance(value, TransactionDate):
        return value.utc
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if require:
        raise ValueError(f"No transaction time supplied for {value}")
    # Local noon is only an ordering convention for date-only ledger entries.
    # Matching uses date_distance(), never this imputed instant.
    DATE_ONLY_NOTES.add(str(value))
    return local_to_utc(datetime.combine(value, datetime.min.time()).replace(hour=12))


def has_precise_time(value) -> bool:
    return isinstance(value, (TransactionDate, datetime))


def calendar_date(value) -> date:
    if isinstance(value, datetime):
        value = tax_date(value)
    return date(value.year, value.month, value.day)


def date_distance(a, b) -> float:
    # Matching requires actual timestamps. Calendar-only values are never
    # promoted to measured instants or matched through a wider date window.
    return abs((utc_time(a, require=True) - utc_time(b, require=True)).total_seconds())


def date_close(a, b) -> bool:
    return date_distance(a, b) <= MATCH_HOURS * 3600


def display_time(value) -> str:
    if isinstance(value, (TransactionDate, datetime)):
        return utc_time(value).isoformat().replace("+00:00", "Z")
    return f"{value.isoformat()} (date only; daily close)"


def display_price(value) -> str:
    """Format console prices without rounding or changing calculation values."""
    whole, _, fraction = format(D(value), 'f').partition('.')
    fraction = fraction.rstrip('0')
    shown = format(int(whole), ',')
    if len(fraction) > 2:
        return shown + '.' + fraction[:2] + '.....'
    return shown + ('.' + fraction.ljust(2, '0') if fraction else '')


# Console announcements only; valuation and HTTP caches remain independent.
# Cleared at configuration so each command-line run displays its own prices.
PRICE_MESSAGES_SHOWN = set()


def price_result(currency, dt, value, source):
    """Announce each distinct requested valuation once, including cached reuse.

    Keep precise instants separate from daily closes. Foreign exchange is
    displayed as units per AUD; the original AUD-per-unit rate used by the
    calculations and report audit is never changed.
    """
    currency = currency.upper()
    value = D(value)
    precise = has_precise_time(dt)
    requested = ('time', utc_time(dt)) if precise else ('date', calendar_date(dt))
    key = (currency, requested, value)
    if key in PRICE_MESSAGES_SHOWN:
        return
    if currency == 'BTC':
        quote = f"AUD${display_price(value)} {currency}"
    else:
        quote = f"1 AUD = {currency}{display_price(Decimal('1') / value)}"
    PRICE_MESSAGES_SHOWN.add(key)
    if not precise:
        print(f"Daily Close Price Lookup: {human_date(dt)} {quote}", flush=True)
        return
    when = utc_time(dt)
    age = re.search(r"; age ([0-9]+(?:\.[0-9]+)?)s(?:$|;)", source)
    kind = 'trade' if 'trade' in source.lower() else 'rate'
    if 'cross' in source.lower() and 'minute' in source.lower() and age:
        closure = 'pre-closure FX; ' if 'pre-closure' in source else ''
        observed = f" using {closure}conversion quotes, oldest available at least {age.group(1)}s prior."
    elif 'minute' in source.lower() and age:
        closure = 'pre-closure ' if 'pre-closure' in source else ''
        observed = f" from {closure}minute quote available at least {age.group(1)}s prior."
    else:
        observed = f" from {kind} observed {age.group(1)}s prior." if age else '.'
    print(f"Price Lookup {human_date(when)} {when:%H:%M:%S}z {quote}{observed}", flush=True)



def http_body_success(body):
    try:
        value = json.loads(body)
        if isinstance(value, dict):
            if value.get("error") or (value.get("chart") or {}).get("error"):
                return False
    except (ValueError, UnicodeDecodeError):
        if b"OBS_VALUE" in body and len(body.strip().splitlines()) < 2:
            return False
    return True


def cached_http(url, headers=None, timeout=None):
    """Cache successful historical responses on disk; never cache failures.

    Credentials stay in request headers, never in keys, logs, or the cache.
    At most two HTTP attempts. Only public-provider rate-limit pacing can
    sleep; it is measured separately. There are no hidden rate-entry prompts.
    """
    global NETWORK_SECONDS, NETWORK_CALLS, CACHE_HITS, THROTTLE_SECONDS, LAST_KRAKEN_REQUEST
    timeout = HTTP_TIMEOUT if timeout is None else timeout
    key = hashlib.sha256(url.encode()).hexdigest()
    with sqlite3.connect(CACHE_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, body BLOB)")
        row = db.execute("SELECT body FROM http_cache WHERE key=?", (key,)).fetchone()
        if row and http_body_success(row[0]):
            CACHE_HITS += 1
            return row[0]
        if row:
            db.execute("DELETE FROM http_cache WHERE key=?", (key,))
    for attempt in range(2):
        started = time.perf_counter()
        try:
            if urllib.parse.urlsplit(url).hostname == "api.kraken.com":
                # Public-provider pacing only, not a processing delay. Cached
                # pages do not wait. Include this in measured network time.
                delay = max(0.0, 1.1 - (time.perf_counter() - LAST_KRAKEN_REQUEST))
                if delay:
                    time.sleep(delay)
                    THROTTLE_SECONDS += delay
                LAST_KRAKEN_REQUEST = time.perf_counter()
            NETWORK_CALLS += 1
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "Bitcoin-CGT-Generator/107", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
            # Public explorer and historical price URLs are immutable. Today's
            # incomplete candles must not persist into a later run.
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            immutable = http_body_success(body) and not ('period2' in params and int(params['period2'][0]) > time.time())
            if urllib.parse.urlsplit(url).hostname == 'api.btcmarkets.net' and 'to' in params:
                query_end = datetime.fromisoformat(params['to'][0].replace('Z', '+00:00'))
                immutable = immutable and query_end.timestamp() <= time.time()
            if urllib.parse.urlsplit(url).hostname == 'www.forexite.com':
                match = re.search(r'/(\d{4})/(\d{2})/(\d{6})\.zip$', urllib.parse.urlsplit(url).path)
                if match:
                    archive_day = datetime.strptime(match[3], '%d%m%y').date()
                    immutable = immutable and archive_day < datetime.now(ZoneInfo('Europe/Berlin')).date()
            if immutable:
                with sqlite3.connect(CACHE_PATH) as db:
                    db.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?)", (key, body))
            return body
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 500, 502, 503, 504} or attempt:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt:
                raise
        finally:
            NETWORK_SECONDS += time.perf_counter() - started
    raise RuntimeError("HTTP request failed")


def held_twelve_months(acquired: date, disposed: date) -> bool:
    # ATO excludes acquisition and disposal dates. Calendar anniversary handles
    # leap years; the first eligible disposal is after the anniversary date.
    try:
        anniversary = date(acquired.year + 1, acquired.month, acquired.day)
    except ValueError:
        anniversary = date(acquired.year + 1, 2, 28)
    return disposed > anniversary


def parse_sheet_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return TransactionDate(local_to_utc(v))
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if re.search(r"[T ]\d{2}:\d{2}", s):
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return TransactionDate(local_to_utc(parsed))
    for f in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    # ODS date-value strings may include a timezone or trailing time.
    m = re.match(r"^(20\d{2}-\d{2}-\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def quarter_key(dt: date) -> str:
    return f"{dt.year}Q{((dt.month - 1) // 3) + 1}"


def tx_key(sheet: str, row: int) -> Tuple[str, int]:
    return sheet, row


def match_bitaroo_deposit_row(reports, wallet_row: "WalletTx") -> Optional[Tuple[Decimal, str, "BitarooRow"]]:
    """Match an outgoing wallet transaction to a Bitaroo Deposit.

    Priority is:
      1. an exact on-chain transaction output amount matching the Bitaroo Deposit;
      2. the identified Bitcoin Core output amount;
      3. the legacy small-difference inference as a last local fallback.

    The Bitaroo PDF transaction type is authoritative: only BTC ``Deposit`` rows
    are candidates. The wallet ``Details`` field is never consulted.
    """
    if not wallet_row.outgoing:
        return None

    info = lookup_transaction_online(wallet_row.txid) if wallet_row.txid else None
    principal = wallet_row.principal
    gross_csv = abs(wallet_row.tx_total)

    # The transaction may contain several outputs. A Bitaroo Deposit must match
    # the *specific wallet output represented by this row*, never merely another
    # output elsewhere in the same transaction. Otherwise a multi-output payment
    # can incorrectly turn an unrelated recipient (for example another person's
    # Bitaroo address) into a taxpayer-owned Bitaroo transfer.
    identified_output = None
    if info is not None and info.outputs:
        identified_output = _identify_onchain_output(info, wallet_row)

    cands = []
    for r in reports.btc_rows:
        if r.tx_type != "Deposit" or r.amount <= 0 or not date_close(tax_date(r.dt), wallet_row.dt):
            continue

        if identified_output is not None:
            output_diff = abs(identified_output - r.amount)
            if output_diff <= Decimal("0.00000002"):
                cands.append((0, date_distance(r.dt, wallet_row.dt), output_diff, r, identified_output, "onchain"))
            elif output_diff <= Decimal("0.00100000"):
                # Bitaroo can credit the exchange deposit slightly below the
                # wallet's sent output when a small exchange-side deposit fee is
                # embedded in the movement. The tolerance is applied only to the
                # specific output identified for this wallet row, so another output
                # in the same transaction can never satisfy the match by accident.
                cands.append((1, date_distance(r.dt, wallet_row.dt), output_diff, r, r.amount, "onchain-net"))
            continue

        # When an on-chain lookup succeeded but this particular blockchain row could not
        # be mapped to one unique output, do not guess from another output in the
        # transaction. An exact quantity/date match remains safe only when there
        # was no usable on-chain output set to disambiguate the transaction.
        if info is not None and info.outputs:
            continue

        # No usable on-chain output evidence: compare the exchange deposit with
        # every authoritative transfer amount. In Historical Wallet ODS, this
        # includes the net quantity implied by Transaction minus the separate Fee.
        amount_candidates = wallet_transfer_amount_candidates(wallet_row)
        exact_added = False
        for amount_index, amount in enumerate(amount_candidates):
            diff = abs(r.amount - amount)
            if diff <= BTC_EPS:
                # Keep the historical primary amount as the first-choice exact
                # match; the additional net-of-fee representation is secondary.
                rank = 1 if amount_index == 0 else 2
                mode = "transfer-amount" if amount_index == 0 else "historical-net"
                cands.append(
                    (rank, date_distance(r.dt, wallet_row.dt), diff, r, r.amount, mode)
                )
                exact_added = True
                break
        if exact_added:
            continue

        # Preserve the legacy small-difference fallbacks after the authoritative
        # exact amount candidates have been exhausted.
        gross_diff = abs(r.amount - gross_csv)
        if Decimal("0") <= gross_csv - r.amount <= Decimal("0.00100000"):
            cands.append((2, date_distance(r.dt, wallet_row.dt), gross_diff, r, r.amount, "embedded"))
        elif Decimal("0") <= r.amount - gross_csv <= Decimal("0.00100000"):
            cands.append((3, date_distance(r.dt, wallet_row.dt), gross_diff, r, r.amount, "csv-net"))

    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1], x[2], x[3].dt))
    _, _, _, row, qty, mode = cands[0]
    return qty, mode, row



# ---------------------------------------------------------------------------
# Source data classes
# ---------------------------------------------------------------------------

@dataclass
class BitarooRow:
    dt: datetime
    tx_type: str
    amount: Decimal
    balance: Decimal
    asset: str
    source_file: str
    page: int
    # Original statement-order occurrence of this row within its (timestamp, trade side)
    # group.  It is populated before chronological normalization so trade proceeds
    # remain paired with the same BTC row even after the ledger is reordered.
    trade_occurrence: Optional[int] = None

    @property
    def buy(self) -> bool:
        return self.asset == "BTC" and self.tx_type in {"Express Buy", "Buy Bitcoin"} and self.amount > 0

    @property
    def sell(self) -> bool:
        return self.asset == "BTC" and self.tx_type in {"Sell Bitcoin", "Express Sell"} and self.amount < 0


@dataclass
class Trade:
    dt: datetime
    side: str
    btc: Decimal
    aud: Decimal
    fee_aud: Decimal
    source_file: str
    occurrence: int


@dataclass
class OtherExchangeRow:
    dt: date
    fiat: Decimal
    btc: Decimal
    currency: str
    exchange: str
    source_file: str
    row_num: int
    network_fee: Decimal = Decimal("0")
    network_fee_unit: str = ""

    @property
    def fees(self):
        return (("Network fee", self.network_fee, self.network_fee_unit),)

    @property
    def btc_fee_amount(self):
        return sum((abs(amount) for _, amount, unit in self.fees if unit == 'BTC'), Decimal('0'))

    @property
    def sold_unit(self):
        return self.currency if self.buy else 'BTC'

    @property
    def received_unit(self):
        return 'BTC' if self.buy else self.currency

    @property
    def ref(self) -> Tuple[str, int]:
        return (self.exchange, self.row_num)

    @property
    def buy(self) -> bool:
        # Direction is deliberately determined ONLY from the signed source
        # amounts. No heuristic based on magnitude, exchange name, BTC value,
        # or destination is permitted.
        return self.fiat <= 0 and self.btc > 0

    @property
    def sell(self) -> bool:
        return self.fiat > 0 and self.btc < 0


@dataclass(frozen=True)
class ChainTxOutput:
    vout: int
    amount: Decimal
    address: str = ""


@dataclass
class ChainTxInfo:
    txid: str
    fee: Optional[Decimal]
    outputs: List[ChainTxOutput]
    input_addresses: List[str] = field(default_factory=list)
    source: str = ""


@dataclass
class WalletTx:
    sheet: str
    row_num: int
    dt: date
    tx_total: Decimal
    fee: Decimal
    balance: Decimal
    details: str
    address: str = ""
    txid: str = ""
    fee_known: bool = True
    is_historical_ods: bool = False
    onchain_output_amount: Optional[Decimal] = None
    onchain_fee_source: str = ""
    onchain_lookup_error: str = ""
    descriptor_id: str = ""
    output_index: int = -1
    accounting_flow: str = ""
    accounting_joint: bool = False

    @property
    def incoming(self) -> bool:
        return self.tx_total > 0

    @property
    def outgoing(self) -> bool:
        return self.tx_total < 0

    @property
    def principal(self) -> Decimal:
        # Bitcoin Core listtransactions reports send/output amount separately
        # from the transaction fee. The fee is NOT subtracted from the output
        # amount. When an on-chain output has been identified for this row, use
        # that authoritative amount; otherwise retain the CSV amount.
        if self.outgoing:
            if self.is_historical_ods:
                return max(Decimal("0"), abs(self.tx_total) - abs(self.fee))
            if self.onchain_output_amount is not None:
                return self.onchain_output_amount
            return abs(self.tx_total)
        return self.tx_total

    @property
    def ref(self) -> Tuple[str, int]:
        return tx_key(self.sheet, self.row_num)


@dataclass
class Lot:
    id: str
    acquired: date
    qty: Decimal
    cost: Decimal
    source: str
    transition_mv: Optional[Decimal] = None
    transition_gain: Optional[Decimal] = None
    trace: str = ""
    # True when an AUD cost base is known. False is used only for historical
    # Bitaroo deposits whose quantity is proven by the exchange statement but
    # whose source wallet/export is not supplied.
    basis_known: bool = True
    # Historical external-person acquisitions before the requested FY keep their
    # market-value price lookup lazy. The dated price is fetched only if this lot
    # is actually consumed by a target-FY calculation.
    deferred_market_price: bool = False
    # A pre-FY Historical Wallet ODS receipt may have an unresolved source owner.
    # The ownership question is deferred until this specific lot reaches a
    # target-FY disposal.
    source_ownership_unresolved: bool = False
    # Original receiving-wallet row for that unresolved Historical ODS receipt.
    source_wallet_ref: Optional[Tuple[str, int]] = None
    # Historical ODS wallet-ownership dependencies accumulated as this lot moves
    # through old wallet-to-wallet transfers.  These are deliberately deferred
    # until the lot is actually consumed by a target-FY disposal.  Each tuple is
    # (source wallet name, transfer date). The newest dependency is checked first;
    # an external answer establishes the ownership boundary and stops the chain.
    deferred_wallet_ownership: Tuple[Tuple[str, date], ...] = ()


@dataclass
class EventComponent:
    date: date
    description: str
    wallet: str
    btc: Decimal
    proceeds: Decimal
    cost: Decimal
    gain_loss: Decimal
    bucket: str
    discounted_gain: Decimal = Decimal("0")
    lot_source: str = ""
    notes: List[str] = field(default_factory=list)
    sale_qty: Decimal = Decimal("0")
    pool_qty: Decimal = Decimal("0")
    sale_proceeds_total: Decimal = Decimal("0")
    gross_gain: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    asset_unit: str = "BTC"


@dataclass
class LedgerEvent:
    when: datetime
    kind: str
    payload: object

# ---------------------------------------------------------------------------
# Bitaroo PDF parser
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"^((?:\d{1,2} [A-Za-z]+ \d{4}|[A-Za-z]+ \d{1,2},? \d{4}) \d{1,2}:\d{2}:\d{2} [AP]M)\s+(.+?)\s+(-?[\d,]+\.\d+)\s+(-?[\d,]+\.\d+)$")
RANGE_RE = re.compile(r"([A-Za-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Za-z]+,? \d{4})\s*-\s*([A-Za-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Za-z]+,? \d{4})")


def clean_text(s: str) -> str:
    s = s.replace("\u00a0", " ").replace("\u2009", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", s).strip()


def _bitaroo_timestamp_balance_tolerance(asset: str) -> Decimal:
    """Return a balance continuity tolerance appropriate to the statement asset.

    BTC balances are printed to 8 decimal places, so a small two-satoshi tolerance
    is enough for statement rounding.  AUD balances are printed to cents and can
    differ by a cent because the statement itself is rounded to displayed money
    precision.
    """
    if asset == "BTC":
        return BITAROO_DUST_BALANCE_EPS
    return Decimal("0.01")


def _canonicalize_bitaroo_timestamp_groups(rows: List[BitarooRow], asset: str) -> List[BitarooRow]:
    """Normalize exact-same-timestamp Bitaroo rows into replay order.

    The PDF parser necessarily preserves the statement's extracted row order so
    same-timestamp AUD/BTC trade legs can first be paired exactly as presented by
    Bitaroo.  The statement can then be replayed chronologically by using the
    Balance column: for a chronological transition A -> B,

        B.balance - B.amount ~= A.balance

    The routine reconstructs the unique balance chain for each exact timestamp
    group.  Zero-amount rows are harmless state markers and are retained ahead of
    the non-zero chain.  If a group cannot be resolved uniquely from its own
    balances, the original statement order is retained rather than inventing an
    ordering.  No date, amount, exchange, or wallet-specific rule is used.
    """
    if len(rows) < 2:
        return rows

    tolerance = _bitaroo_timestamp_balance_tolerance(asset)
    normalized: List[BitarooRow] = []
    i = 0

    while i < len(rows):
        j = i + 1
        while j < len(rows) and rows[j].dt == rows[i].dt:
            j += 1

        group = rows[i:j]
        nonzero = [r for r in group if r.amount != 0]
        if len(nonzero) <= 1:
            normalized.extend(group)
            i = j
            continue

        def previous_balance(row: BitarooRow) -> Decimal:
            return row.balance - row.amount

        # The first chronological row is the one whose implied pre-balance is
        # not the post-balance of another row in the same timestamp group.
        starts = [
            row for row in nonzero
            if not any(
                other is not row
                and abs(previous_balance(row) - other.balance) <= tolerance
                for other in nonzero
            )
        ]
        if len(starts) != 1:
            # Ambiguous/malformed statement data: keep the source order rather
            # than making an unsupported chronological guess.
            normalized.extend(group)
            i = j
            continue

        remaining = list(nonzero)
        ordered: List[BitarooRow] = [starts[0]]
        remaining.remove(starts[0])
        current = starts[0]
        resolvable = True

        while remaining:
            candidates = sorted(
                [
                    (
                        abs(previous_balance(row) - current.balance),
                        row,
                    )
                    for row in remaining
                    if abs(previous_balance(row) - current.balance) <= tolerance
                ],
                key=lambda item: item[0],
            )

            if not candidates:
                resolvable = False
                break

            # Prefer the closest balance transition.  This handles a displayed
            # 1-satoshi/1-cent rounding difference without overriding an exact
            # transition.  An exact tie remains ambiguous and falls back to the
            # original statement order.
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                resolvable = False
                break

            current = candidates[0][1]
            ordered.append(current)
            remaining.remove(current)

        if not resolvable:
            normalized.extend(group)
        else:
            # Zero-amount rows (notably BTC Deposit Fee rows) do not change the
            # balance, so their relative position cannot affect the BTC quantity
            # chain.  Keep them before the reconstructed non-zero state changes.
            normalized.extend([row for row in group if row.amount == 0])
            normalized.extend(ordered)

        i = j

    return normalized


def parse_bitaroo_pdf(path: str, asset: str):
    reader = PdfReader(path)
    page_texts = [(p.extract_text(extraction_mode="layout") or "") for p in reader.pages]
    full = "\n".join(page_texts)
    m = RANGE_RE.search(clean_text(full))
    if not m:
        raise ValueError(f"Could not find financial-year range in {path}")
    def parse_report_date(s: str) -> date:
        s = s.replace(',', '')
        for fmt in ('%d %B %Y', '%B %d %Y'):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        raise ValueError(f'Could not parse report date: {s}')
    start = parse_report_date(m.group(1))
    end = parse_report_date(m.group(2))

    starting = None
    closing = None
    sm = re.search(r"Starting Balance:\s*([\d,]+\.\d+)", full)
    cm = re.search(r"Closing Balance:\s*([\d,]+\.\d+)", full)
    if sm:
        starting = Decimal(sm.group(1).replace(",", ""))
    if cm:
        closing = Decimal(cm.group(1).replace(",", ""))

    rows: List[BitarooRow] = []
    for page_no, page_text in enumerate(page_texts, 1):
        for raw in page_text.splitlines():
            line = clean_text(raw)
            mm = DATE_RE.match(line)
            if not mm:
                continue
            tm = re.match(r"^(.*?) (\d{1,2}:\d{2}:\d{2} [AP]M)$", mm.group(1))
            if not tm:
                continue
            date_text = tm.group(1).replace(",", "")
            time_text = tm.group(2)
            try:
                base_date = datetime.strptime(date_text, "%d %B %Y")
            except ValueError:
                base_date = datetime.strptime(date_text, "%B %d %Y")
            dt = datetime.combine(base_date.date(), datetime.strptime(time_text, "%I:%M:%S %p").time())
            rows.append(BitarooRow(dt, mm.group(2).strip(), Decimal(mm.group(3).replace(",", "")),
                                    Decimal(mm.group(4).replace(",", "")), asset, path, page_no))
    # Keep the PDF extraction order intact here.  Bitaroo statements can contain
    # several rows with exactly the same timestamp, and those rows are displayed
    # in statement order rather than true chronological replay order.  Trade legs
    # must be paired using that original statement order before the ledger is
    # normalized; ReportSet.add() performs that normalization after pair_trades().
    rows.sort(key=lambda r: r.dt)
    return rows, starting, closing, (start, end)


def pair_trades(aud_rows: List[BitarooRow], btc_rows: List[BitarooRow]) -> List[Trade]:
    """Pair Bitaroo AUD and BTC trade legs.

    The statements supplied use UTC but the AUD and BTC PDFs can differ by
    one second for the same trade. Match within two seconds, preserving the
    statement order when several trades share a timestamp.
    """
    aud_candidates: Dict[Tuple[date, str], List[Tuple[int, BitarooRow]]] = defaultdict(list)
    for idx, r in enumerate(aud_rows):
        if r.tx_type in {"Express Buy", "Buy Bitcoin", "Sell Bitcoin", "Express Sell"}:
            aud_candidates[(tax_date(r.dt), r.tx_type)].append((idx, r))

    used = set()
    out: List[Trade] = []
    occurrence: Dict[Tuple[datetime, str], int] = defaultdict(int)
    for r in btc_rows:
        if r.tx_type not in {"Express Buy", "Buy Bitcoin", "Sell Bitcoin", "Express Sell"}:
            continue
        key_type = r.tx_type
        candidates = []
        for idx, a in aud_candidates.get((tax_date(r.dt), key_type), []):
            if idx in used:
                continue
            delta = abs((a.dt - r.dt).total_seconds())
            if delta <= 2:
                candidates.append((delta, idx, a))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[0], x[1]))
        _, idx, a = candidates[0]
        used.add(idx)
        side = "buy" if r.amount > 0 else "sell"
        occ_key = (r.dt, side)
        occ = occurrence[occ_key]
        occurrence[occ_key] += 1
        # Preserve the original statement-order pairing on the BTC row itself.
        # The ledger is chronologically normalized later, but this reference keeps
        # the exact AUD proceeds attached to the same BTC trade row.
        r.trade_occurrence = occ
        out.append(Trade(r.dt, side, abs(r.amount), abs(a.amount), Decimal("0"), r.source_file, occ))
    return sorted(out, key=lambda t: (t.dt, t.occurrence))


def sell_fees(aud_rows: List[BitarooRow]) -> Dict[Tuple[datetime, int], Decimal]:
    fee_rows = defaultdict(list)
    for r in aud_rows:
        if r.tx_type == "Sell Fee" and r.amount < 0:
            fee_rows[r.dt].append(abs(r.amount))
    return fee_rows

# ---------------------------------------------------------------------------
# Descriptor blockchain + Historical Wallet ODS wallet input
# ---------------------------------------------------------------------------


def ods_cell_text(cell: TableCell) -> str:
    date_value = cell.getAttribute("datevalue")
    if date_value not in (None, ""):
        return str(date_value).strip()
    ps = cell.getElementsByType(P)
    if ps:
        chunks = []
        for p in ps:
            chunks.append("".join(str(n.data) for n in p.childNodes if hasattr(n, "data")))
        text = "".join(chunks)
    else:
        text = ""
    if text:
        return text.strip()
    value = cell.getAttribute("value")
    if value not in (None, ""):
        return str(value).strip()
    date_value = cell.getAttribute("datevalue")
    if date_value not in (None, ""):
        return str(date_value).strip()
    return ""







def ods_cells(row, width, where):
    """Bounded ODS expansion; reject populated data outside the input schema."""
    from odf import teletype
    cells, column = [], 0
    for cell in row.getElementsByType(TableCell):
        count = int(cell.getAttribute('numbercolumnsrepeated') or 1)
        if column + count > width and (teletype.extractText(cell).strip() or cell.getAttribute('value') or cell.getAttribute('datevalue') or cell.getAttribute('timevalue') or cell.getAttribute('formula')):
            raise ValueError(f"{where}: unexpected populated columns after column {width}.")
        cells.extend([cell] * min(count, max(0, width-column)))
        column += count
    return cells


def ods_utc_timestamp(day_cell, time_cell, where):
    """Combine separate explicitly UTC cells without the machine's timezone.

    Supports native ODS dates/times, clock text and numeric day-fraction times.
    No midnight/noon default is allowed for a missing clock value.
    """
    from odf import teletype
    try:
        raw_day = day_cell.getAttribute('datevalue') or teletype.extractText(day_cell).strip()
        day = parse_sheet_date(raw_day)
        if day is None or has_precise_time(day):
            raise ValueError('Date (UTC) must contain a calendar date only')
        raw_time = time_cell.getAttribute('timevalue')
        if raw_time:
            duration = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', raw_time)
            if not duration or not any(duration.groups()):
                raise ValueError('invalid native ODS time')
            hours, minutes, seconds = duration.groups()
            if int(hours or 0)>23 or int(minutes or 0)>59 or Decimal(seconds or 0)>=60:
                raise ValueError('time outside a UTC day')
            elapsed = Decimal(hours or 0)*3600 + Decimal(minutes or 0)*60 + Decimal(seconds or 0)
            clock = (datetime.min + timedelta(microseconds=int(elapsed*1_000_000))).time()
        elif time_cell.getAttribute('value') is not None:
            fraction = Decimal(time_cell.getAttribute('value'))
            if not fraction.is_finite() or not 0 <= fraction < 1:
                raise ValueError('numeric time must be a fraction of one day')
            micros = int((fraction*86400*1_000_000).to_integral_value())
            if micros>=86400*1_000_000:
                raise ValueError('time rounds beyond a UTC day')
            clock = (datetime.min + timedelta(microseconds=micros)).time()
        else:
            value = teletype.extractText(time_cell).strip()
            if not re.fullmatch(r'(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?[zZ]?', value):
                raise ValueError('Time (UTC) is missing/invalid; use HH:MM:SS')
            value=value.split(':',1)[0].zfill(2)+':'+value.split(':',1)[1]
            value=value.rstrip('zZ')
            clock_format='%H:%M' if len(value.split(':'))==2 else ('%H:%M:%S.%f' if '.' in value else '%H:%M:%S')
            clock=datetime.strptime(value,clock_format).time()
        return TransactionDate(datetime.combine(day, clock, tzinfo=UTC))
    except (ValueError, TypeError, ArithmeticError, OverflowError) as exc:
        raise ValueError(f"{where}: invalid UTC date/time: {exc}") from exc


def load_historical_wallet_ods(path: str, client=None, defer_joint=False) -> Dict[str, List[WalletTx]]:
    """Wallet names in row 1; exact owned TXID:output_index values below."""
    from historical_outpoints import load_wallets
    return load_wallets(sys.modules[__name__], path, client, defer_joint=defer_joint)


# ISO fiat unit codes are format/domain rules, never user/exchange identities.
# Other tickers are treated as crypto assets; no worksheet-specific rules apply.
FIAT_UNITS = set("""AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN
BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BYR BZD CAD CDF CHE CHF CHW CLF
CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP
GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK
JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD
MDL MGA MKD MMK MNT MOP MRU MRO MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK
NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RUR RWF SAR SBD SCR
SDG SEK SGD SHP SLE SLL SOS SRD SSP STN STD SVC SYP SZL THB TJS TMT TND TOP
TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VEF VES VND VUV WST XAF
XCD XCG XOF XPF YER ZAR ZMW ZWG ZWL""".split())


def exchange_asset_is_fiat(row):
    return row.currency in FIAT_UNITS


def load_other_exchange_ods(path: str) -> List[OtherExchangeRow]:
    """Read exactly Date (UTC) | Time (UTC) | sold ticker | Network Fee | bought ticker on every tab.

    C/D are signed outflows (negative or zero); E is net received. Fees
    use the outgoing crypto unit (incoming BTC for fiat purchases);
    sold-unit fees are additional to C;
    bought-unit fees are withheld from the gross acquisition/consideration. One row represents one transaction; matching
    uses amounts/dates from supplied wallet and exchange records. No wallet,
    receipt-list, currency-format or legacy schema metadata is read.
    """
    from odf import teletype
    out = []
    reserved = {'DATE', 'FEE', 'FIAT', 'BITCOIN', 'WALLET', 'RECEIPTS', 'NOTES', 'STATUS', 'ASSET TYPE'}
    def text(cell):
        return teletype.extractText(cell).strip()
    def populated(cell):
        return bool(text(cell) or cell.getAttribute('value') or cell.getAttribute('datevalue') or cell.getAttribute('formula'))
    def five_cells(row, location):
        # Inspect the compressed tail without expanding thousands of blank ODS
        # cells. A populated sixth column is an error, even with a blank header.
        cells, column = [], 0
        for cell in row.getElementsByType(TableCell):
            repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
            if column + repeat > 5 and populated(cell):
                raise ValueError(f"{location}: exactly five columns are allowed: Date (UTC) | Time (UTC) | sold unit | Network Fee | bought unit. Remove extra populated columns.")
            cells.extend([cell] * min(repeat, max(0, 5-column)))
            column += repeat
        return cells
    for table in load_ods(path).spreadsheet.getElementsByType(Table):
        exchange = (table.getAttribute('name') or Path(path).stem).strip()
        rows = table.getElementsByType(TableRow)
        if not rows:
            continue
        location = f"{Path(path).name} sheet '{exchange}'"
        if not any(populated(c) for c in rows[0].getElementsByType(TableCell)):
            if any(populated(c) for r in rows[1:] for c in r.getElementsByType(TableCell)):
                raise ValueError(f"{location}: data has no headings; row 1 must be exactly Date (UTC) | Time (UTC) | sold unit | Network Fee | bought unit.")
            continue
        header = [text(c).upper() for c in five_cells(rows[0], location)]
        if len(header) != 5 or header[:2] != ['DATE (UTC)','TIME (UTC)']:
            raise ValueError(f"{location}: row 1 must be exactly Date (UTC) | Time (UTC) | sold unit | Network Fee | bought unit.")
        sold_unit, received_unit = header[2], header[4]
        if header[3] != 'NETWORK FEE':
            raise ValueError(f"{location}: column D must be Network Fee.")
        # A network fee belongs to the outgoing on-chain crypto asset. A fiat
        # purchase has no outgoing blockchain asset, so it uses incoming BTC.
        # This is one unit-based convention for all input tabs, not a venue rule.
        for unit in (sold_unit, received_unit):
            if not re.fullmatch(r'[A-Z][A-Z0-9]{1,11}', unit) or unit in reserved:
                raise ValueError(f"{location}: use an asset ticker such as AUD, USD, BTC or BCH in the asset headings; got {unit!r}.")
        if (sold_unit == 'BTC') == (received_unit == 'BTC'):
            raise ValueError(f"{location}: exactly one asset column must be BTC; other-asset-only trades need a separate asset ledger.")
        for row_num, row in enumerate(rows[1:], 2):
            where = f"{location} row {row_num}"
            cells = five_cells(row, where)
            if not any(populated(c) for c in cells):
                continue
            def cell_text(i):
                return text(cells[i]) if i < len(cells) else ''
            def number(i, label):
                raw = cells[i].getAttribute('value') if i < len(cells) else None
                if raw in (None, ''):
                    raw = cell_text(i)
                try:
                    value = D(str(raw).replace(',', '').strip())
                    if not value.is_finite():
                        raise ValueError('non-finite amount')
                    return value
                except Exception as exc:
                    raise ValueError(f"{where}: missing/invalid {label}; enter a normal number (use 0 for no fee).") from exc
            if len(cells)<5:
                raise ValueError(f"{where}: all five exchange columns must be present.")
            dt = ods_utc_timestamp(cells[0], cells[1], where)
            sold, network_fee, received = number(2, sold_unit), number(3, 'Network Fee'), number(4, received_unit)
            # Unit headings remain fixed within a sheet. Signed amounts allow
            # purchases and sales on the same exchange tab without venue rules.
            if network_fee > 0 or not ((sold <= 0 and received > 0) or (sold > 0 and received <= 0)):
                raise ValueError(f"{where}: one asset amount must be negative (or zero for a free receipt), the other positive; Network Fee must be negative or zero.")
            btc = sold if sold_unit == 'BTC' else received
            fiat = received if sold_unit == 'BTC' else sold
            if btc == 0 or (btc < 0 and fiat <= 0):
                raise ValueError(f"{where}: a BTC disposal needs negative BTC and positive consideration.")
            outgoing_unit = sold_unit if sold < 0 else received_unit
            incoming_unit = received_unit if received > 0 else sold_unit
            fee_unit = incoming_unit if outgoing_unit in FIAT_UNITS else outgoing_unit
            other_unit = received_unit if sold_unit == 'BTC' else sold_unit
            out.append(OtherExchangeRow(dt, fiat, btc, other_unit, exchange, path, row_num, network_fee, fee_unit))
    if not out:
        raise ValueError(f"No exchange rows found in {Path(path).name}.")
    out.sort(key=lambda r:(utc_time(r.dt), r.exchange.casefold(), r.row_num))
    return out


def match_other_exchange_sale(rows,amount,dt):
    candidates=[r for r in rows if r.sell and abs(abs(r.btc)-abs(amount))<=Decimal('0.00000002') and date_close(r.dt,dt)]
    if len(candidates)>1:raise ValueError('Multiple supplied exchange sales match one transfer; provide unambiguous amounts/times.')
    return candidates[0] if candidates else None


def classify_other_exchange_ownership(rows: List[OtherExchangeRow], taxpayer: str) -> List[OtherExchangeRow]:
    """Ask who owns each populated exchange tab and discard non-taxpayer tabs.

    Ownership is deliberately decided at the exchange-tab level, not row by row.
    This matches the structure of the Other Exchanges workbook: each worksheet is
    a transaction list for one exchange and one person.
    """
    if not rows:
        return []

    grouped: Dict[str, List[OtherExchangeRow]] = defaultdict(list)
    for row in rows:
        grouped[row.exchange].append(row)

    included: List[OtherExchangeRow] = []
    print("\nOther Exchange spreadsheet ownership")
    for exchange in sorted(grouped, key=str.casefold):
        tab_rows = grouped[exchange]
        print(f"\nThe Other Exchanges spreadsheet contains exchange data for: {exchange}")
        print(f"Does this exchange belong to {taxpayer}?")
        while True:
            raw = prompt_input("Enter Y if it is, or N if it belongs to somebody else: ").strip().lower()
            if raw in {"y", "yes"}:
                included.extend(tab_rows)
                break
            if raw in {"n", "no"}:
                break
            print("Please enter Y or N.")

    included.sort(key=lambda x: (x.dt, x.exchange.lower(), x.row_num))
    excluded = len(rows) - len(included)
    print(f"\nIncluded Other Exchange rows: {len(included)}")
    if excluded:
        print(f"Discarded Other Exchange rows belonging to somebody else: {excluded}")
    return included




def merge_wallets(*wallet_maps: Dict[str, List[WalletTx]]) -> Dict[str, List[WalletTx]]:
    out: Dict[str, List[WalletTx]] = {}
    for mapping in wallet_maps:
        for name, rows in mapping.items():
            if name in out:
                raise ValueError(f"Duplicate wallet name encountered: {name}. Give each supplied wallet a distinct name.")
            out[name] = rows
    return out


def wallet_transfer_amount_candidates(outgoing: WalletTx) -> List[Decimal]:
    """Net transferred BTC; historical ODS gross debits exclude the Fee column.

    Bitcoin Core rows prefer their identified on-chain output amount. Miner
    fees remain separate from both forms of net principal.
    """
    return [abs(outgoing.principal)]


def _wallet_transfer_match_score(outgoing: WalletTx, incoming: WalletTx) -> Optional[Decimal]:
    """Return the best BTC-quantity difference for an outgoing/incoming pair."""
    if not outgoing.outgoing or not incoming.incoming:
        return None
    target = incoming.principal
    diffs = [abs(amount - target) for amount in wallet_transfer_amount_candidates(outgoing)]
    best = min(diffs)
    return best if best <= Decimal("0.00000002") else None


def find_loaded_wallet_counterparty(
    wallets: Dict[str, List[WalletTx]],
    wallet_name: str,
    row: WalletTx,
) -> Optional[Tuple[str, int]]:
    """Find a unique counterpart wallet using transaction evidence only.

    No wallet Details/description text is read. A supplied Bitcoin Core TXID is
    preferred; if there is no usable cross-wallet TXID, the function falls back
    to a unique BTC quantity/date match across every loaded wallet, including
    Historical Wallet ODS ledgers. Historical ODS fees are used only through the
    dedicated Fee column when converting a gross debit to the net transferred
    quantity.
    """
    row_is_outgoing = row.outgoing
    candidates = []

    # Exact transaction-ID matching is strongest when the receiving/source row
    # has a real TXID. ODS synthetic row IDs are unique to their own source file
    # and therefore naturally do not collide with CSV TXIDs.
    if row.txid:
        for other_wallet, rows in wallets.items():
            if other_wallet == wallet_name and not row.accounting_flow:
                continue
            for idx, candidate in enumerate(rows):
                if row_is_outgoing and not candidate.incoming:
                    continue
                if not row_is_outgoing and not candidate.outgoing:
                    continue
                if candidate.txid != row.txid:
                    continue
                if (row.accounting_flow or candidate.accounting_flow) and row.accounting_flow != candidate.accounting_flow:
                    continue
                if row.output_index>=0 and candidate.output_index>=0 and row.output_index!=candidate.output_index:
                    continue
                if not date_close(candidate.dt, row.dt):
                    continue

                if row_is_outgoing:
                    diff = _wallet_transfer_match_score(row, candidate)
                else:
                    diff = _wallet_transfer_match_score(candidate, row)

                # A real TXID plus the same destination/source address is stronger
                # evidence than a small CSV amount discrepancy. Bitcoin Core exports
                # can represent one side of the transaction with a slightly different
                # amount (for example because the receiving wallet records its net
                # credited amount). In that situation, matching the exact TXID and
                # exact address still identifies the counterpart wallet without
                # guessing from unrelated quantity/date coincidences.
                address_exact = bool(row.address and candidate.address and row.address == candidate.address)
                if diff is None and not address_exact:
                    continue

                quantity_penalty = Decimal('0') if diff is not None else Decimal('1')
                address_penalty = 0 if address_exact else 1
                effective_diff = diff if diff is not None else Decimal('999999999')
                candidates.append((0, address_penalty, quantity_penalty, other_wallet.casefold(), idx, effective_diff))

        if candidates:
            best_key = min((c[0], c[1], c[2], c[5]) for c in candidates)
            best = [c for c in candidates if (c[0], c[1], c[2], c[5]) == best_key]
            if len(best) == 1:
                _, _, _, source_name, idx, _ = best[0]
                for name in wallets:
                    if name.casefold() == source_name:
                        return name, idx
            return None

    # Fallback: quantity/date only, but accept only a unique best-day candidate.
    fallback: List[Tuple[int, str, int, Decimal]] = []
    for other_wallet, rows in wallets.items():
        if other_wallet == wallet_name:
            continue
        for idx, candidate in enumerate(rows):
            if (row.accounting_flow or candidate.accounting_flow) and row.accounting_flow != candidate.accounting_flow:
                continue
            if (re.fullmatch(r"[0-9a-fA-F]{64}", row.txid or "")
                    and re.fullmatch(r"[0-9a-fA-F]{64}", candidate.txid or "")
                    and row.txid.lower() != candidate.txid.lower()):
                continue
            if row.txid == candidate.txid and row.output_index >= 0 and candidate.output_index >= 0 and row.output_index != candidate.output_index:
                continue
            if row_is_outgoing:
                if not candidate.incoming:
                    continue
                diff = _wallet_transfer_match_score(row, candidate)
            else:
                if not candidate.outgoing:
                    continue
                diff = _wallet_transfer_match_score(candidate, row)
            if diff is None or not date_close(candidate.dt, row.dt):
                continue
            day_delta = date_distance(candidate.dt, row.dt)
            fallback.append((day_delta, other_wallet.casefold(), idx, diff))

    if not fallback:
        return None
    best_day = min(x[0] for x in fallback)
    best_day_rows = [x for x in fallback if x[0] == best_day]
    best_diff = min(x[3] for x in best_day_rows)
    best = [x for x in best_day_rows if x[3] == best_diff]
    if len(best) != 1:
        return None
    _, source_name, idx, _ = best[0]
    for name in wallets:
        if name.casefold() == source_name:
            return name, idx
    return None






def _bitaroo_deposit_candidates(reports, w: WalletTx) -> List[BitarooRow]:
    if not w.outgoing:
        return []

    # Historical ODS gross debits are normalized to net principal by WalletTx.
    # No mining fee is subtracted from an already identified CSV output.
    amount_candidates = wallet_transfer_amount_candidates(w)
    if not amount_candidates:
        return []

    candidates = []
    for r in reports.btc_rows:
        if r.tx_type != "Deposit" or r.amount <= 0 or not date_close(tax_date(r.dt), w.dt):
            continue

        scored = sorted(
            (abs(r.amount - amount), idx)
            for idx, amount in enumerate(amount_candidates)
        )
        diff, amount_index = scored[0]

        if diff <= BTC_EPS:
            # Preserve the pre-V92 priority of the primary wallet amount. The
            # additional net-of-fee representation is a secondary exact match.
            rank = 0 if amount_index == 0 else 1
        elif diff <= Decimal("0.00100000"):
            rank = 2
        else:
            continue

        candidates.append((rank, date_distance(r.dt, w.dt), diff, r))

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3].dt))
    return [x[3] for x in candidates]


def _bitaroo_withdrawal_candidates(reports, w: WalletTx) -> List[BitarooRow]:
    if not w.incoming:
        return []
    qty = w.tx_total
    candidates = []
    for r in reports.btc_rows:
        if r.tx_type != "Withdrawal" or r.amount >= 0 or not date_close(tax_date(r.dt), w.dt):
            continue
        diff = abs(abs(r.amount) - qty)
        if diff <= Decimal("0.00000002"):
            candidates.append((date_distance(r.dt, w.dt), diff, r))
    candidates.sort(key=lambda x: (x[0], x[1], x[2].dt))
    return [x[2] for x in candidates]


def match_bitaroo_withdrawal_row(reports, wallet_row: WalletTx) -> Optional[BitarooRow]:
    """Match an incoming wallet row to a Bitaroo withdrawal using BTC/date only."""
    candidates = _bitaroo_withdrawal_candidates(reports, wallet_row)
    return candidates[0] if candidates else None


def refresh_wallet_metadata(wallets,reports):
    """Descriptor rows already carry decoded output values and the actual miner fee."""
    for rows in wallets.values():
        for row in rows:
            if row.descriptor_id and (not row.fee_known or row.fee < 0):
                raise ValueError('Descriptor ledger contains an unresolved network fee.')






def match_other_exchange_buy_to_wallet_receipt(
    rows: Iterable[OtherExchangeRow],
    wallet_row: WalletTx,
    used_refs: Optional[set[Tuple[str, int]]] = None,
    max_date: Optional[date] = None,
) -> Optional[OtherExchangeRow]:
    """Match an Other Exchange buy to a wallet receipt using only authoritative
    quantity/date evidence and the wallet's runtime ownership classification.

    BTC quantity must be within the established 2e-8 tolerance and the dates must
    be within the configured elapsed-time tolerance. No wallet name, worksheet name, person name, or
    exchange name is used as an ownership signal. When several buys satisfy the
    test, no source is guessed.
    """
    used_refs = used_refs or set()
    tolerance = Decimal("0.00000002")
    candidates: List[OtherExchangeRow] = []
    for row in rows:
        if not row.buy or row.ref in used_refs:
            continue
        if max_date is not None and row.dt > max_date:
            continue
        if abs(row.btc-wallet_row.principal) > tolerance:
            continue
        if not date_close(row.dt, wallet_row.dt):
            continue
        candidates.append(row)

    candidates.sort(key=lambda r: (date_distance(r.dt, wallet_row.dt), r.exchange.lower(), r.row_num))
    if len(candidates) == 1:
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Report collection / preloaded history tracing
# ---------------------------------------------------------------------------

class ReportSet:
    def __init__(self):
        self.years: Dict[int, Tuple[str, str]] = {}
        self.ranges: Dict[int, Tuple[date, date]] = {}
        self.start_bal: Dict[int, Optional[Decimal]] = {}
        self.close_bal: Dict[int, Optional[Decimal]] = {}
        self.btc_rows: List[BitarooRow] = []
        self.aud_rows: List[BitarooRow] = []
        self.trades: List[Trade] = []
        self.fee_rows: Dict[datetime, List[Decimal]] = defaultdict(list)
        self.opening_lots: Dict[int, List[Tuple[date, Decimal, Decimal, str]]] = defaultdict(list)

    def add(self, fy_start: int, aud: str, btc: str):
        if fy_start in self.years:
            return
        arows, _astart, _aclose, arange = parse_bitaroo_pdf(aud, "AUD")
        brows, bstart, bclose, brange = parse_bitaroo_pdf(btc, "BTC")
        if arange != brange:
            raise ValueError(f"AUD and BTC reports do not cover the same FY: {aud} / {btc}")
        self.years[fy_start] = (aud, btc)
        self.ranges[fy_start] = brange
        self.start_bal[fy_start] = bstart
        self.close_bal[fy_start] = bclose

        # Pair AUD/BTC trade legs using the original statement order first.
        # Then normalize the exact-timestamp rows for chronological ledger replay.
        # This is the critical distinction: the statement's pairing order and the
        # ledger's replay order are not always the same when several rows share the
        # exact same timestamp.
        trades = pair_trades(arows, brows)
        statement_fee_rows = sell_fees(arows)
        arows = _canonicalize_bitaroo_timestamp_groups(arows, "AUD")
        brows = _canonicalize_bitaroo_timestamp_groups(brows, "BTC")

        # A Bitaroo BTC statement can begin with a non-zero opening balance,
        # while the AUD statement for the *previous* Financial Year contains
        # the acquisition immediately before that opening balance. We defer
        # reconstruction until all currently-loaded reports are known; see
        # reconstruct_opening_lots().
        self.aud_rows.extend(arows)
        self.btc_rows.extend(brows)
        self.trades.extend(trades)
        # Keep sell-fee occurrence order aligned with Trade.occurrence, which is
        # intentionally based on the original statement order.
        for dt, fees in statement_fee_rows.items():
            self.fee_rows[dt].extend(fees)
        self.aud_rows.sort(key=lambda r: r.dt)
        self.btc_rows.sort(key=lambda r: r.dt)
        self.trades.sort(key=lambda t: (t.dt, t.occurrence))

    def reconstruct_opening_lots(self):
        """Reconstruct Bitaroo opening BTC lots from the preceding AUD report.

        This is specifically needed for the common Bitaroo reporting pattern
        where the BTC FY report starts on 1 July with a non-zero balance, while
        the purchase that created that balance appears on 30 June in the
        previous FY's AUD report. The reconstruction is derived from the
        supplied statements for the adjacent Financial Years and never relies
        on a taxpayer-specific date or amount.
        """
        for fy, rng in self.ranges.items():
            if (self.start_bal.get(fy) or Decimal("0")) <= BTC_EPS:
                continue
            if self.opening_lots.get(fy):
                continue

            bstart = self.start_bal[fy]
            # An AUD buy shortly before the opening boundary, constrained to
            # the same 24-hour window rather than a whole calendar day.
            cands = [a for a in self.aud_rows
                     if a.tx_type in {"Express Buy", "Buy Bitcoin"}
                     and a.amount < 0
                     and 0 <= (local_to_utc(datetime.combine(rng[0], datetime.min.time())) - utc_time(a.dt)).total_seconds() <= MATCH_HOURS * 3600]

            # Prefer a purchase from the immediately preceding FY.
            cands.sort(key=lambda a: (date_distance(local_to_utc(datetime.combine(rng[0], datetime.min.time())), a.dt),
                                      0 if tax_date(a.dt).year == rng[0].year - 1 else 1,
                                      a.dt), reverse=False)

            if len(cands) == 1:
                a = cands[0]
                self.opening_lots[fy].append(
                    (tax_date(a.dt), bstart, abs(a.amount),
                     f"Bitaroo opening balance reconstructed from AUD buy {human_date(tax_date(a.dt))}"))
            elif cands:
                # If several candidates exist on the same boundary date, use
                # the one whose BTC/AUD implied quantity most closely matches
                # the opening BTC balance.
                scored = []
                for a in cands:
                    # Search same-day BTC buy rows when available.
                    implied_qtys = [abs(r.amount) for r in self.btc_rows
                                    if r.tx_type in {"Express Buy", "Buy Bitcoin"}
                                    and tax_date(r.dt) == tax_date(a.dt)]
                    if implied_qtys:
                        err = min(abs(q - bstart) for q in implied_qtys)
                    else:
                        err = Decimal("999999")
                    scored.append((err, a))
                scored.sort(key=lambda x: (x[0], x[1].dt))
                if scored[0][0] <= Decimal("0.00000002"):
                    a = scored[0][1]
                    self.opening_lots[fy].append(
                        (tax_date(a.dt), bstart, abs(a.amount),
                         f"Bitaroo opening balance reconstructed from AUD buy {human_date(tax_date(a.dt))}"))




# ---------------------------------------------------------------------------
# Wallet ownership prompts
# ---------------------------------------------------------------------------

class MissingWalletDataError(RuntimeError):
    """Raised when the evidence shows that a wallet/address owned by the taxpayer is missing."""


MISSING_DESTINATION_DATA_MESSAGE = "All user wallet and exchange information must be provided. Please update the data with the correct information and try the script again"


class MissingDestinationDataError(MissingWalletDataError):
    """A personally owned destination has no supplied transaction history."""


@lru_cache(maxsize=8192)
def lookup_transaction_online(txid: str) -> Optional[ChainTxInfo]:
    """Look up a Bitcoin transaction from public explorers.

    Mempool.space documents GET /api/tx/:txid as returning fee, inputs and
    outputs. Blockstream's Esplora API exposes the same transaction fields as a
    fallback. All amounts returned by the explorers are converted from satoshis
    to BTC here.

    This lookup is cached by txid so multiple wallet blockchain rows belonging to the
    same transaction result in a single public request per endpoint.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{64}", txid or ""):
        return None

    urls = [
        (f"https://mempool.space/api/tx/{urllib.parse.quote(txid)}", "mempool.space"),
        (f"https://blockstream.info/api/tx/{urllib.parse.quote(txid)}", "blockstream.info"),
    ]
    for url, source in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Bitcoin-CGT-Generator/40",
                    "Accept": "application/json",
                },
                method="GET",
            )
            raw = cached_http(url, dict(req.header_items()), timeout=8).decode("utf-8", errors="replace")
            data = json.loads(raw)
            fee_sat = data.get("fee")
            fee = Decimal(str(fee_sat)) / Decimal("100000000") if fee_sat is not None else None
            outputs: List[ChainTxOutput] = []
            for idx, vout in enumerate(data.get("vout", [])):
                # Mempool.space/Esplora normally expose scriptpubkey_address;
                # tolerate the alternative key spelling defensively.
                addr = (vout.get("scriptpubkey_address") or vout.get("scriptpubkey_address_string") or "")
                value_sat = vout.get("value")
                if value_sat is None:
                    value_sat = vout.get("value_sat")
                if value_sat is None:
                    continue
                outputs.append(
                    ChainTxOutput(idx, Decimal(str(value_sat)) / Decimal("100000000"), str(addr or ""))
                )

            input_addresses: List[str] = []
            for vin in data.get("vin", []):
                prevout = vin.get("prevout") or {}
                addr = prevout.get("scriptpubkey_address") or prevout.get("scriptpubkey_address_string")
                if addr and addr not in input_addresses:
                    input_addresses.append(str(addr))

            return ChainTxInfo(txid, fee, outputs, input_addresses, source)
        except Exception:
            continue
    return None


@lru_cache(maxsize=4096)
def lookup_transaction_input_addresses(txid: str) -> List[str]:
    """Return public input addresses for the existing ownership-source check."""
    info = lookup_transaction_online(txid)
    return list(info.input_addresses) if info is not None else []


def _output_matches_row(output: ChainTxOutput, wallet_row: "WalletTx") -> bool:
    if wallet_row.address and output.address and wallet_row.address == output.address:
        return True
    # When a descriptor-derived record has no usable address, an exact amount match is still
    # useful, but only within the already identified transaction. This is not a
    # quantity-only cross-transaction match.
    if abs(output.amount - abs(wallet_row.tx_total)) <= Decimal("0.00000002"):
        return True
    return False


def _identify_onchain_output(info: Optional[ChainTxInfo], wallet_row: "WalletTx") -> Optional[Decimal]:
    if info is None or not wallet_row.outgoing:
        return None
    matches = [o for o in info.outputs if _output_matches_row(o, wallet_row)]
    if not matches:
        return None
    # Prefer an address match, then the smallest amount difference.
    matches.sort(key=lambda o: (0 if wallet_row.address and o.address == wallet_row.address else 1, abs(o.amount - abs(wallet_row.tx_total)), o.vout))
    return matches[0].amount



class WalletOwnership:
    """Single, cached ownership knowledge base for wallets and addresses.

    Wallets are either:
      * personal  - belongs to the taxpayer and participates in personal CGT tracing
      * external  - is not owned by the taxpayer and is treated as an ownership boundary

    A wallet classification is asked for once and reused everywhere in the run.
    Bitcoin-address decisions are likewise cached so an address answered "N" once
    is never asked about again later in the trace.
    """
    PERSONAL = "personal"
    EXTERNAL = "external"

    def __init__(self, taxpayer: str):
        self.taxpayer = taxpayer or "the taxpayer"
        self.wallet_statuses: Dict[str, str] = {"Bitaroo": self.PERSONAL}
        self.address_statuses: Dict[str, str] = {}
        self.source_tx_statuses: Dict[str, str] = {}
        self.destination_statuses = {}
        self.source_address_cache: Dict[str, List[str]] = {}
        self.report_wallets: set[str] = set()
        # Populated solely by the dependency graph. Historical ODS wallets are not
        # assumed to belong to the taxpayer; they become explicit ownership
        # dependencies only when the graph demonstrates that their ownership can
        # affect the selected CGT trace.
        self.required_wallet_ownership: set[str] = set()
        self._wallets: Optional[Dict[str, List[WalletTx]]] = None
        self.descriptor_wallet_ids: Dict[str, str] = {}

    def bind_wallets(self, wallets: Dict[str, List[WalletTx]]) -> None:
        """Attach the loaded wallet map so descriptor identities can be resolved lazily."""
        self._wallets = wallets

    @staticmethod
    def _ask_yes_no(prompt: str) -> bool:
        while True:
            raw = prompt_input(prompt).strip().lower()
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            print("Please enter Y or N.")

    @staticmethod
    def _normalise_status(value) -> str:
        if value is True:
            return WalletOwnership.PERSONAL
        if value is False:
            return WalletOwnership.EXTERNAL
        v = str(value).strip().lower()
        if v in {"personal", "owned", "taxpayer", "y", "yes"}:
            return WalletOwnership.PERSONAL
        if v in {"external", "other", "someone else", "n", "no"}:
            return WalletOwnership.EXTERNAL
        raise ValueError(f"Unknown wallet ownership status: {value!r}")




    def ensure_wallet_identity(self,wallets,wallet,trace_source=""):
        return wallet

    def _is_historical_ods_only(self, wallet: str) -> bool:
        if wallet == "Bitaroo" or self._wallets is None:
            return False
        rows = self._wallets.get(wallet)
        return bool(rows) and all(row.is_historical_ods for row in rows)

    def mark_wallet_ownership_required(
        self,
        wallet: str,
        trace_source: str = "Exchange Tracer",
    ) -> str:
        """Record that the trace has established this wallet as an ownership dependency."""
        if wallet == "Bitaroo":
            return wallet
        self.required_wallet_ownership.add(wallet)
        return wallet

    def wallet_ownership_is_required(self, wallet: str) -> bool:
        if wallet == "Bitaroo":
            return False
        return wallet in self.required_wallet_ownership

    def dependency_requires_ownership(
        self,
        wallet: str,
        row_dt: date,
        period_start: date,
        period_end: date,
        parent_wallet: Optional[str] = None,
    ) -> bool:
        """Determine whether reaching ``wallet`` requires an immediate ownership answer.

        Descriptor wallets are always interactive once the dependency graph reaches them.
        A Historical Wallet ODS tab is interactive immediately only when the
        transaction that caused the dependency is itself inside the selected
        Financial Year.

        For older Historical ODS rows, ownership is deliberately *not* inferred
        from the parent wallet and is not requested during historical replay. The
        LedgerEngine carries the Historical ODS wallet through the actual parcel
        lineage and asks ownership only when that parcel is consumed by a target-FY
        disposal. This prevents an unrelated historical wallet graph from turning
        into blanket ownership questions while still allowing an external-wallet
        boundary to terminate the trace at the point where it matters.
        """
        if wallet == "Bitaroo":
            return False
        if not self._is_historical_ods_only(wallet):
            return True
        return period_start <= row_dt <= period_end

    def ownership_required_for_row(self, wallet: str, dt: date, period_start: date, period_end: date) -> bool:
        """Return whether an already-reached wallet needs explicit ownership."""
        return self.dependency_requires_ownership(wallet, dt, period_start, period_end)

    def planning_wallet_status(
        self,
        wallet: str,
        require_ownership: bool = False,
        trace_source: str = "Exchange Tracer",
    ) -> str:
        """Return a wallet classification appropriate to the current tracing context.

        Historical Wallet ODS tabs can be encountered merely as provenance while
        replaying old transactions that ultimately feed a target-year disposal. In
        that provenance-only context, ownership is deliberately *not* assumed and
        no question is asked: the accounting engine can replay the tab without
        creating an ownership decision.

        When ``require_ownership`` is True, the caller has established that this
        wallet itself is an active dependency of the target-year CGT ledger (for
        example, a wallet supplying or receiving BTC in an active target trace).
        An ODS-only wallet must then be explicitly classified by the taxpayer.

        This keeps the two requirements separate and generic: do not ask about an
        irrelevant historical wallet, but never silently assume ownership of a
        wallet once its ownership actually affects a target-year CGT disposal.
        """
        if wallet == "Bitaroo":
            return self.PERSONAL
        existing = self.wallet_statuses.get(wallet)
        if existing is not None:
            status = self._normalise_status(existing)
            if require_ownership and wallet != "Bitaroo":
                self.report_wallets.add(wallet)
            return status
        if self._is_historical_ods_only(wallet) and not require_ownership:
            # Provisional replay classification only. Do not cache this as an
            # ownership decision, because the historical tab may never contribute
            # BTC to a target-year disposal.
            return self.PERSONAL
        status = self.wallet_status(wallet, trace_source=trace_source)
        if wallet != "Bitaroo":
            self.report_wallets.add(wallet)
        return status

    def _ask_wallet_status(
        self,
        wallet: str,
        trace_source: str = "Exchange Tracer",
    ) -> str:
        print(f"\nThe {trace_source} has discovered {wallet}. Does this wallet belong to {self.taxpayer}?")
        owned = self._ask_yes_no("Enter Y if it does, or N if it belongs to somebody else or is an SMSF Wallet: ")
        return self.PERSONAL if owned else self.EXTERNAL

    def wallet_status(
        self,
        wallet: str,
        trace_source: str = "Exchange Tracer",
    ) -> str:
        if wallet == "Bitaroo":
            return self.PERSONAL
        existing = self.wallet_statuses.get(wallet)
        if existing is not None:
            status = self._normalise_status(existing)
            self.wallet_statuses[wallet] = status
            return status
        status = self._ask_wallet_status(wallet, trace_source=trace_source)
        self.wallet_statuses[wallet] = status
        return status

    def decide_unresolved_destination(self, origin, dt, amount, transaction_id,
                                      address="", trace_source="Exchange Tracer", decision_ref=None) -> bool:
        """Ask about a missing destination identically for either trace."""
        key = ('address', address) if address else ('row', origin, decision_ref if decision_ref is not None else transaction_id)
        known = self.address_statuses.get(address) if address else None
        if known is None:
            known = self.destination_statuses.get(key)
        if known is not None:
            if known == self.PERSONAL:
                raise MissingDestinationDataError(MISSING_DESTINATION_DATA_MESSAGE)
            return False
        decision_key = (transaction_id, origin, address, str(amount), str(decision_ref))
        owned = getattr(self, '_run_trace_lookup', lambda *args: None)('destination', decision_key)
        if owned is None:
            print(f"\n{trace_source.upper()} — UNRESOLVED BITCOIN DESTINATION")
            print(f"Origin: {origin}")
            print(f"Date UTC: {utc_time(dt):%d/%m/%Y}" if has_precise_time(dt) else f"Date UTC: {dt:%d/%m/%Y}")
            print(f"Time UTC: {utc_time(dt):%H:%M:%S}" if has_precise_time(dt) else "Time UTC: Not supplied")
            print(f"Amount sent: {qb(amount)} BTC")
            print(f"Transaction ID: {transaction_id}")
            if address:
                print(f"Destination address: {address}")
            print("No supplied wallet or exchange matches this outgoing transaction.")
            print(f"Is the receiver/destination wallet for this transaction owned by {self.taxpayer}?")
            owned = self._ask_yes_no("Enter Y if it is, or N if it belongs to somebody else: ")
            getattr(self, '_run_trace_remember', lambda *args: None)('destination', decision_key, owned)
        else:
            print(f"Reusing confirmed {'Yes' if owned else 'No'} destination evidence for {self.taxpayer}: {transaction_id}.")
        status = self.PERSONAL if owned else self.EXTERNAL
        self.destination_statuses[key] = status
        if address:
            self.address_statuses[address] = status
        if owned:
            raise MissingDestinationDataError(MISSING_DESTINATION_DATA_MESSAGE)
        return False

    def decide_external_address(self, w: WalletTx,
                                trace_source: str = "Exchange Tracer") -> bool:
        return self.decide_unresolved_destination(
            w.sheet, w.dt, w.principal,
            w.txid or f"WALLET:{w.sheet}:row{w.row_num}",
            address=w.address, trace_source=trace_source, decision_ref=w.ref,
        )

    def decide_incoming_source(
        self,
        w: WalletTx,
        trace_source: str = "Exchange Tracer",
    ) -> bool:
        """Decide whether an unresolved incoming transaction originates from the taxpayer's holdings."""
        txid = w.txid or f"{w.sheet}:{w.row_num}"

        # A previously classified source transaction never asks again.
        tx_known = self.source_tx_statuses.get(txid)
        if tx_known is not None:
            if tx_known == self.PERSONAL:
                raise MissingWalletDataError(
                    f"Missing wallet transaction information: {w.sheet} received {qb(w.principal)} BTC on {human_date(w.dt)} "
                    f"in transaction {w.txid or 'unknown'}, and you previously confirmed the source is owned by {self.taxpayer}. "
                    "Provide the missing wallet export/transaction information and rerun the calculation."
                )
            return False

        addresses = self.source_address_cache.get(txid)
        if addresses is None:
            addresses = lookup_transaction_input_addresses(w.txid) if w.txid else []
            self.source_address_cache[txid] = list(addresses)

        # Reuse any address classification already known. If all discovered
        # source addresses are external, no question is necessary.
        known_statuses = [self.address_statuses[a] for a in addresses if a in self.address_statuses]
        if known_statuses and all(s == self.EXTERNAL for s in known_statuses) and len(known_statuses) == len(addresses):
            self.source_tx_statuses[txid] = self.EXTERNAL
            return False
        if any(s == self.PERSONAL for s in known_statuses):
            self.source_tx_statuses[txid] = self.PERSONAL
            raise MissingWalletDataError(
                f"Missing wallet transaction information: {w.sheet} received {qb(w.principal)} BTC on {human_date(w.dt)} "
                f"in transaction {w.txid or 'unknown'}, and one or more known source addresses are owned by {self.taxpayer}. "
                "Provide the missing wallet export/transaction information and rerun the calculation."
            )

        decision_key = (txid, tuple(sorted(addresses)))
        owned = getattr(self, '_run_trace_lookup', lambda *args: None)('source', decision_key)
        if owned is None:
            print(f"\n{trace_source.upper()} — UNRESOLVED BITCOIN SOURCE")
            print(f"Receiving wallet: {w.sheet}")
            print(f"Date UTC: {utc_time(w.dt):%d/%m/%Y}" if has_precise_time(w.dt) else f"Date UTC: {w.dt:%d/%m/%Y}")
            print(f"Time UTC: {utc_time(w.dt):%H:%M:%S}" if has_precise_time(w.dt) else "Time UTC: Not supplied")
            print(f"Amount received: {qb(w.principal)} BTC")
            print(f"Transaction ID: {w.txid or '<not available>'}")
            if addresses:
                print("Source/input address(es) found from the public transaction:")
                for addr in addresses:
                    print(f"  {addr}")
                print(f"Are these source address(es) owned by {self.taxpayer}?")
            else:
                print("No supplied wallet matches this incoming transaction and it is not a matched Bitaroo withdrawal.")
                print("No source address could be established from the supplied historical record or blockchain transaction.")
                print(f"Is the sender/source wallet for this transaction owned by {self.taxpayer}?")

            owned = self._ask_yes_no("Enter Y if it is, or N if it belongs to somebody else: ")
            getattr(self, '_run_trace_remember', lambda *args: None)('source', decision_key, owned)
        else:
            print(f"Reusing confirmed {'Yes' if owned else 'No'} source evidence for {self.taxpayer}: {txid}.")
        status = self.PERSONAL if owned else self.EXTERNAL
        self.source_tx_statuses[txid] = status
        # A group answer does not identify which individual addresses are owned.
        if len(addresses) == 1:
            self.address_statuses.setdefault(addresses[0], status)

        if owned:
            shown = ", ".join(addresses) if addresses else (f"transaction {w.txid}" if w.txid else "the unresolved source")
            raise MissingWalletDataError(
                f"Missing wallet transaction information: {w.sheet} received {qb(w.principal)} BTC on {human_date(w.dt)} "
                f"from {shown}, but the corresponding source wallet transaction was not supplied. "
                f"You confirmed the source is owned by {self.taxpayer}. Provide the missing wallet export/transaction information and rerun the calculation."
            )
        return False


class HistoryPlanner:
    """Work backwards only through BTC that can feed a target-year disposal.

    The wallet CSVs + Historical Wallet ODS supply the wallet chain. For each relevant wallet disposal,
    use the wallet's balance history to find the active holding episode (after
    its last zero balance). Every incoming parcel in that episode is relevant:
    - From another wallet -> recurse into that wallet.
    - Bitaroo withdrawal -> identify it by authoritative BTC quantity/date evidence and
      inspect the corresponding Bitaroo holding episode.
      If the Bitaroo statement proves an intervening Deposit but no matching
      source-wallet export is supplied, V24 preserves that quantity as an
      unreconciled lot instead of dropping it from the exchange holding pool.
    - Incoming wallet rows that match an owned Other Exchange buy by exact BTC
      quantity/date -> treat as an original exchange acquisition;
      do not recurse into a missing source wallet.
    - An unresolved Historical Wallet ODS receipt that is actually reached by this
      target-year dependency graph still requires the normal source-ownership
      question. If the user says the source is external, the parcel becomes an
      external acquisition and its historical BTC/AUD lookup remains deferred until
      a target-FY disposal actually consumes it. Historical ODS rows in wallets that
      are never reached by the target-year dependency graph are never questioned.

    For a Bitaroo sale/withdrawal/fee, inspect the Bitaroo holding episode in
    the loaded report. If that episode reaches the report opening balance,
    require the preceding FY report. This is intentionally not a simple
    year-by-year walk, so an FY is requested only when the trace needs it.
    """
    def __init__(self, wallets: Dict[str, List[WalletTx]], target_fy: int, ownership: Optional[WalletOwnership] = None,
                 other_exchange_rows: Optional[List[OtherExchangeRow]] = None, reports=None):
        self.wallets = wallets
        self.ownership = ownership or WalletOwnership("the taxpayer")
        self.other_exchange_rows = other_exchange_rows or []
        self.reports = reports
        self.target_fy = target_fy
        self.start, self.end = fy_bounds(target_fy)
        self.required_fys: set[int] = set()
        self.required_wallet_rows: set = set()
        self.required_bitaroo_keys: set[Tuple[int, datetime, str, str]] = set()
        self.required_bitaroo_rows: set[int] = set()
        self.wallet_queue = deque()
        self.wallet_row_trace_source: Dict[Tuple[str, int], str] = {}
        self.primary_required_wallet_rows: set = set()
        self.primary_required_bitaroo_rows: set = set()
        self.bitaroo_queue = deque()
        # Preserve the provenance of each queued Bitaroo dependency so that an
        # Auxiliary wallet trace cannot silently re-enter the graph as a Primary
        # Trace merely because a helper function enqueued another related row.
        self.bitaroo_key_trace_source: Dict[Tuple[int, datetime, str, str], str] = {}
        # FYs recorded here reached the earliest supplied Bitaroo statement for
        # a required historical holding episode, but the immediately preceding
        # FY is not present in the user's statement directory.  This is an
        # ordinary data boundary (for example, a taxpayer may have joined
        # Bitaroo after its account opening), not evidence that a particular FY
        # must exist.  The trace therefore stops at that supplied opening balance
        # instead of failing the entire calculation.
        self.unreconstructed_opening_boundaries: Dict[int, Decimal] = {}

    def fy_for_date(self, d: date) -> int:
        return d.year if d.month >= 7 else d.year - 1

    @staticmethod
    def exchange_balance_is_zero(row: BitarooRow) -> bool:
        # Bitaroo withdrawal fees commonly leave exactly 0.00010000 BTC dust.
        # The supplied manual working paper treats these post-fee exchange
        # balances as exhausted for subsequent cost-base tracing. The fee itself
        # is still processed separately by the engine when it is relevant.
        return abs(row.balance) <= Decimal("0.00010001") and row.tx_type in {"Withdrawal", "Withdrawal Fee"}

    def wallet_episode_before_row(self, wallet: str, row_idx: int) -> List[Tuple[int, WalletTx]]:
        """Return the holding episode relevant to ``row_idx``.

        For a target disposal, only transactions up to and including that
        disposal can affect its cost base. Looking forward into later financial
        years can otherwise make the dependency planner request future Bitaroo
        statements merely because a wallet remains open after the target event.
        The backwards boundary is still the previous zero balance, because that
        determines the start of the active proportional holding pool.
        """
        rows = self.wallets[wallet]
        if not rows or row_idx < 0 or row_idx >= len(rows):
            return []

        start_idx = 0
        for i in range(row_idx - 1, -1, -1):
            if abs(rows[i].balance) <= BTC_EPS:
                # Include the zeroing transaction itself. A later inbound parcel
                # can be posted on the same workbook date, so the old episode
                # must be exhausted before the new parcel is credited.
                start_idx = i
                break

        return list(enumerate(rows[start_idx:row_idx + 1], start=start_idx))


    def enqueue_wallet(
        self,
        wallet: str,
        row_idx: int,
        require_ownership: bool = False,
        trace_source: str = "Exchange Tracer",
    ):
        # A Descriptor wallet is named and classified only when the dependency graph has
        # actually discovered a transaction that can affect the requested FY.
        # Historical ODS ownership is marked separately when the graph proves that
        # the wallet is an explicit ownership dependency.
        if wallet not in self.wallets or row_idx < 0 or row_idx >= len(self.wallets[wallet]):
            return
        if self.wallets[wallet][row_idx].dt > self.end:
            return
        if require_ownership:
            self.ownership.mark_wallet_ownership_required(wallet, trace_source=trace_source)
        key = (wallet, row_idx)
        ref = self.wallets[wallet][row_idx].ref
        self.wallet_row_trace_source.setdefault(ref, trace_source)
        if ref in self.required_wallet_rows:
            return
        self.required_wallet_rows.add(ref)
        self.wallet_queue.append(key)

    def enqueue_target_year_wallet_outgoings(
        self,
        wallet: str,
        trace_source: str = "Exchange Tracer",
    ) -> None:
        """Activate all target-year outgoing transactions from a reached wallet.

        Once a wallet has been established as belonging to the taxpayer and has
        become part of the target-year CGT dependency graph, every outgoing
        transaction from that wallet during the selected Financial Year is itself
        a potential CGT disposal. Limiting the scan to the historical holding
        episode that first caused the wallet to be reached can otherwise omit a
        later gift or other external transfer from the same wallet.
        """
        if wallet not in self.wallets or wallet == "Bitaroo":
            return
        for idx, row in enumerate(self.wallets[wallet]):
            if not row.outgoing:
                continue
            if self.start <= row.dt <= self.end:
                self.enqueue_wallet(
                    wallet,
                    idx,
                    require_ownership=False,
                    trace_source=trace_source,
                )

    def enqueue_wallet_transaction_outputs(
        self,
        wallet: str,
        row_idx: int,
        trace_source: str = "Exchange Tracer",
    ) -> None:
        """Activate every outgoing output in a target-year wallet transaction.

        A Bitcoin transaction can have several outputs. Once one output in a
        target-year transaction is established as part of the taxpayer's CGT
        dependency graph, the other outputs from that same wallet transaction are
        also potential disposals (or internal transfers) and must be considered.
        This is driven entirely by the transaction ID and target-year dates; it
        does not identify any particular wallet, address, FY, or transaction amount.
        """
        if wallet not in self.wallets or row_idx < 0 or row_idx >= len(self.wallets[wallet]):
            return
        anchor = self.wallets[wallet][row_idx]
        if not re.fullmatch(r'[0-9a-fA-F]{64}', anchor.txid or ''):
            return
        if not (self.start <= anchor.dt <= self.end):
            return
        for idx, candidate in enumerate(self.wallets[wallet]):
            if idx == row_idx:
                continue
            if candidate.txid != anchor.txid or candidate.dt > self.end or not candidate.outgoing:
                continue
            self.enqueue_wallet(
                wallet,
                idx,
                require_ownership=False,
                trace_source=trace_source,
            )

    def enqueue_bitaroo(
        self,
        fy: int,
        dt: datetime,
        qty: Decimal,
        kind: str,
        trace_source: str = "Exchange Tracer",
    ):
        # The dependency graph may walk backwards without a historical floor, but it
        # must never walk forwards beyond the end of the requested Financial Year.
        if dt.date() > self.end:
            return
        key = (fy, dt, kind, str(qb(qty)))
        self.bitaroo_key_trace_source.setdefault(key, trace_source)
        if key in self.required_bitaroo_keys:
            return
        self.required_bitaroo_keys.add(key)
        self.required_fys.add(fy)
        self.bitaroo_queue.append((fy, dt, qty, kind))

    def seed(self, reports: "ReportSet"):
        # All target-year Bitaroo sales are relevant disposals.
        for r in reports.btc_rows:
            if r.sell and self.start <= tax_date(r.dt) <= self.end:
                self.enqueue_bitaroo(self.target_fy, r.dt, abs(r.amount), "sale")
            elif r.tx_type == "Withdrawal Fee" and r.amount < 0 and self.start <= tax_date(r.dt) <= self.end:
                self.enqueue_bitaroo(self.target_fy, r.dt, abs(r.amount), "fee")
            elif r.tx_type == "Withdrawal" and r.amount < 0 and self.start <= tax_date(r.dt) <= self.end:
                self.enqueue_bitaroo(self.target_fy, r.dt, abs(r.amount), "withdrawal")

        # Do not scan every loaded wallet merely because it has a target-year
        # outgoing transaction.  That makes unrelated wallets look relevant and
        # forces ownership questions before the CGT dependency graph has reached
        # them.
        #
        # Target-year wallet activity becomes relevant only when the graph reaches
        # that wallet from an authoritative target disposal / holding episode:
        #   * a Bitaroo Deposit in a required exchange episode identifies its source
        #     wallet and enqueues that wallet;
        #   * a transfer from an already-required wallet identifies a destination
        #     wallet and enqueues it only after its ownership is established; or
        #   * an already-required wallet's own episode contains the transaction.
        #
        # This is intentionally generic. No FY, wallet name, CSV filename, date,
        # or transaction amount is used as a special case.

        # Other Exchange purchases are deliberately NOT used to seed wallet
        # history globally. An exchange purchase only becomes part of the wallet
        # dependency graph when a target-year CGT disposal has already caused the
        # relevant wallet episode to be traversed. At that point
        # process_wallet_queue() matches the incoming wallet receipt to the owned
        # Other Exchange buy by BTC quantity and the configured elapsed-time tolerance.




    def find_loaded_wallet_source_for_incoming(
        self,
        receiving_wallet: str,
        incoming: WalletTx,
    ) -> Optional[Tuple[str, int]]:
        """Compatibility wrapper around the Details-free wallet source matcher."""
        if not incoming.incoming:
            return None
        return find_loaded_wallet_counterparty(self.wallets, receiving_wallet, incoming)

    def process_wallet_queue(self):
        while self.wallet_queue:
            wallet, row_idx = self.wallet_queue.popleft()
            row_ref = self.wallets[wallet][row_idx].ref
            trace_source = self.wallet_row_trace_source.get(row_ref, "Exchange Tracer")

            # Ownership is asked only when the backtrace has actually reached this
            # wallet.  An external/SMSF wallet is a boundary: its historical rows
            # do not become part of the taxpayer's personal CGT chain.
            if wallet != "Bitaroo":
                row_dt = self.wallets[wallet][row_idx].dt
                require = (
                    self.ownership.wallet_ownership_is_required(wallet)
                    or self.ownership.ownership_required_for_row(wallet, row_dt, self.start, self.end)
                )
                if self.ownership.planning_wallet_status(wallet, require_ownership=require, trace_source=trace_source) != WalletOwnership.PERSONAL:
                    continue

            # Once this wallet is confirmed as part of the taxpayer's target-year
            # graph, all of its target-year outgoing transfers are potential CGT
            # disposals and must be activated. This remains lazy because wallets
            # that are never reached are still untouched.
            self.enqueue_target_year_wallet_outgoings(wallet, trace_source=trace_source)

            episode = self.wallet_episode_before_row(wallet, row_idx)
            for i, w in episode:
                self.required_wallet_rows.add(self.wallets[wallet][i].ref)
                self.enqueue_wallet_transaction_outputs(
                    wallet,
                    i,
                    trace_source=trace_source,
                )

                if w.incoming:
                    # Other Exchange purchases are acquisitions when their supplied
                    # BTC/date evidence uniquely matches this personal wallet receipt.
                    require = self.ownership.ownership_required_for_row(wallet, w.dt, self.start, self.end)
                    if self.ownership.planning_wallet_status(wallet, require_ownership=require, trace_source=trace_source) == WalletOwnership.PERSONAL:
                        if match_other_exchange_buy_to_wallet_receipt(self.other_exchange_rows, w, max_date=self.end) is not None:
                            continue

                    # Bitaroo withdrawal: direct quantity/date evidence, no Details label.
                    # This is checked before the generic quantity/date wallet-source fallback
                    # so an exchange withdrawal remains authoritative when both happen to
                    # have the same amount/date.
                    withdrawal = match_bitaroo_withdrawal_row(self.reports, w) if self.reports is not None else None
                    if withdrawal is not None:
                        self.enqueue_bitaroo(
                            self.fy_for_date(tax_date(withdrawal.dt)),
                            withdrawal.dt,
                            abs(withdrawal.amount),
                            "withdrawal",
                            trace_source=trace_source,
                        )
                        continue

                    # Historical wallet sources do not always appear in the receiving
                    # receiving row. Search every loaded wallet dynamically by
                    # transaction ID first, then by quantity/date. This is especially
                    # important for historical ODS rows, which have no Bitcoin Core TXID.
                    loaded_source = self.find_loaded_wallet_source_for_incoming(wallet, w)
                    if loaded_source is not None:
                        src_wallet, src_idx = loaded_source
                        src_dt = self.wallets[src_wallet][src_idx].dt
                        require = self.ownership.dependency_requires_ownership(
                            src_wallet, src_dt, self.start, self.end, parent_wallet=wallet
                        )
                        src_status = self.ownership.planning_wallet_status(src_wallet, require_ownership=require, trace_source=trace_source)
                        if src_status == WalletOwnership.PERSONAL:
                            self.enqueue_wallet(src_wallet, src_idx, require_ownership=require, trace_source=trace_source)
                        # An explicitly classified external source is already known
                        # to be outside the taxpayer's personal wallet chain, so the
                        # receipt remains an external acquisition without a further prompt.
                        continue

                    # A pre-FY Historical Wallet ODS receipt is only historical
                    # provenance at this stage. Do not ask source ownership merely
                    # because the dependency planner traversed the old holding
                    # episode: that parcel may be exhausted before the requested FY
                    # and never reach a target-year disposal. The ledger engine carries
                    # this unresolved source forward and asks the normal ownership
                    # question only if that specific lot is actually disposed of in
                    # the requested FY. Current-FY unresolved incoming rows retain
                    # the existing immediate source-resolution behaviour.
                    if w.is_historical_ods and w.dt < self.start:
                        continue

                    self.ownership.decide_incoming_source(
                        w,
                        trace_source=trace_source,
                    )
                    continue

                # Outgoing: follow a loaded wallet destination first using TXID or
                # unique BTC/date evidence; no wallet Details text is consulted.
                dest_match = find_loaded_wallet_counterparty(self.wallets, wallet, w)
                if dest_match is not None:
                    dest, dest_idx = dest_match
                    dest_dt = self.wallets[dest][dest_idx].dt
                    # Historical outgoing movements only reduce the source-wallet
                    # holding episode; their destination ownership is not needed to
                    # backtrace the source cost base. For a transaction inside the
                    # target FY, however, the destination can determine whether the
                    # transfer itself is taxable, so it becomes an explicit dependency.
                    require = self.ownership.dependency_requires_ownership(
                        dest, dest_dt, self.start, self.end
                    ) if self.start <= w.dt <= self.end else False
                    if require:
                        if self.ownership.planning_wallet_status(dest, require_ownership=True, trace_source=trace_source) == WalletOwnership.PERSONAL:
                            self.enqueue_wallet(
                                dest,
                                dest_idx,
                                require_ownership=True,
                                trace_source=trace_source,
                            )
                    continue

                # Otherwise match a Bitaroo deposit directly from BTC/date evidence.
                deposit = match_bitaroo_deposit_row(self.reports, w) if self.reports is not None else None
                if deposit is not None:
                    continue

                if match_other_exchange_sale(self.other_exchange_rows,w.principal,w.dt) is not None:
                    continue
                # Any other unresolved destination is outside the supplied wallet set.
                if w.address:
                    self.ownership.decide_external_address(w, trace_source=trace_source)


    def bitaroo_episode_before(self, reports: "ReportSet", fy: int, dt: datetime, qty: Decimal, kind: str) -> Optional[List[Tuple[int, BitarooRow]]]:
        """Return the complete Bitaroo holding episode containing a required row.

        A required acquisition/sale/withdrawal is not enough by itself: later
        rows in the same continuous exchange balance may exhaust or relocate the
        coins. Those rows have to be replayed too, otherwise an old lot can be
        left artificially available for a later target-year disposal.
        """
        rows = reports.btc_rows
        candidates=[]
        for i,r in enumerate(rows):
            if kind == "withdrawal":
                ok = r.tx_type == "Withdrawal" and r.amount < 0 and abs(r.amount) == qty
            elif kind == "sale":
                ok = r.sell and abs(r.amount) == qty
            elif kind == "fee":
                ok = r.tx_type == "Withdrawal Fee" and r.amount < 0 and abs(r.amount) == qty
            elif kind == "deposit":
                ok = r.tx_type == "Deposit" and r.amount > 0 and r.amount == qty
            else:
                ok = False
            if not ok:
                continue
            if dt.time() == datetime.min.time():
                if date_close(r.dt, dt):
                    candidates.append((date_distance(r.dt, dt), i))
            elif r.dt == dt:
                candidates.append((0, i))
        if not candidates:
            return None
        candidates.sort(key=lambda x:(x[0],x[1]))
        idx=candidates[0][1]
        self.required_bitaroo_rows.add(id(rows[idx]))

        # Find the beginning of the continuous Bitaroo balance episode. Exact-
        # timestamp Withdrawal + Withdrawal Fee rows are one exchange transaction
        # and must be treated atomically when looking backwards for the previous
        # genuine zero/dust boundary.
        start=0
        if idx > 0:
            target_row = rows[idx]
            withdrawal_group = target_row.tx_type in {"Withdrawal", "Withdrawal Fee"}
            for i in range(idx-1,-1,-1):
                rr=rows[i]
                # Exact-timestamp Bitaroo withdrawal + withdrawal-fee rows form
                # one atomic exchange transaction.  After chronological
                # normalization the fee row follows the withdrawal row, and the
                # fee's post-balance may be 0 or 0.00001000 BTC.  That temporary
                # dust balance is an end-of-transaction state, not the beginning
                # of a new historical holding episode.  Treat the whole exact-time
                # withdrawal group as one unit while searching backwards for the
                # previous genuine zero/dust boundary.
                if (withdrawal_group
                        and rr.dt == target_row.dt
                        and rr.tx_type in {"Withdrawal", "Withdrawal Fee"}):
                    continue
                if abs(rr.balance) <= BITAROO_DUST_BALANCE_EPS or self.exchange_balance_is_zero(rr):
                    start=i+1
                    break

        # Find the end of the same continuous episode. Include the row that
        # actually brings the exchange balance to zero/dust so its disposal or
        # withdrawal is replayed and the old lots cannot leak into a later episode.
        end=min(len(rows)-1, idx)
        for i in range(idx, len(rows)):
            rr=rows[i]
            if tax_date(rr.dt) > self.end:
                # The target-year backtrace stops at the end of the requested FY.
                # A later balance zero or disposal belongs to a different FY and
                # cannot be used to reconstruct the target year's cost basis.
                end=i-1
                break
            # Some Bitaroo PDFs list the withdrawal-fee row immediately before
            # its matching withdrawal row at the same timestamp. The fee row can
            # show a zero balance even though the withdrawal row still needs to
            # be replayed to actually move the BTC out of the exchange. Do not
            # close the episode at that intermediate fee row.
            if (rr.tx_type == "Withdrawal Fee"
                    and i + 1 < len(rows)
                    and rows[i + 1].dt == rr.dt
                    and rows[i + 1].tx_type == "Withdrawal"):
                continue
            if abs(rr.balance) <= BITAROO_DUST_BALANCE_EPS or self.exchange_balance_is_zero(rr):
                end=i
                break

        episode=list(enumerate(rows[start:end+1], start=start))
        for _, rr in episode:
            self.required_bitaroo_rows.add(id(rr))

        # If the required row reaches the beginning of a loaded statement and
        # the statement itself opens with a non-zero balance, a preceding FY is
        # useful only when that FY is actually present in the supplied reports.
        # Do not manufacture a missing-statement requirement: a user can quite
        # legitimately have Bitaroo history beginning with a non-zero opening
        # balance on the earliest statement they supplied.  In that case the
        # supplied opening balance is the natural historical boundary and any
        # unresolved cost basis is surfaced only if the parcel later reaches a
        # target-year disposal.
        if start == 0:
            bal_before = rows[idx-1].balance if idx > 0 else (reports.start_bal.get(fy) or Decimal("0"))
            if bal_before > BTC_EPS and not reports.opening_lots.get(fy):
                predecessor_fy = fy - 1
                if predecessor_fy in reports.years:
                    self.required_fys.add(predecessor_fy)
                else:
                    self.unreconstructed_opening_boundaries[fy] = bal_before
        return episode

    def process_bitaroo_queue(self, reports: "ReportSet"):
        remaining=[]
        while self.bitaroo_queue:
            fy, dt, qty, kind = self.bitaroo_queue.popleft()
            bitaroo_key = (fy, dt, kind, str(qb(qty)))
            trace_source = self.bitaroo_key_trace_source.get(
                bitaroo_key,
                "Exchange Tracer",
            )
            if fy not in reports.years:
                remaining.append((fy,dt,qty,kind))
                continue
            episode=self.bitaroo_episode_before(reports,fy,dt,qty,kind)
            if episode is None:
                continue
            for _,r in episode:
                if r.tx_type == "Deposit" and r.amount>0:
                    # Find the wallet row that matches this exact Bitaroo Deposit by
                    # quantity/date. Wallet Details text is not used.
                    # This reverse index is immutable for this planner: match
                    # every outgoing row once, not once per deposit/queue pass.
                    if not hasattr(self,'_deposit_sources'):
                        self._deposit_sources={}
                        for wallet,rows in self.wallets.items():
                            for i,w in enumerate(rows):
                                if w.dt>self.end or not w.outgoing:continue
                                match=match_bitaroo_deposit_row(reports,w)
                                if match is not None:
                                    dep=match[2]
                                    self._deposit_sources.setdefault(id(dep),[]).append((date_distance(w.dt,dep.dt),wallet,i))
                        for candidates in self._deposit_sources.values():candidates.sort()
                    cands=self._deposit_sources.get(id(r),[])
                    if cands:
                        _,wallet,i=cands[0]
                        source_dt = self.wallets[wallet][i].dt
                        require = self.ownership.dependency_requires_ownership(
                            wallet, source_dt, self.start, self.end
                        ) if self.start <= tax_date(r.dt) <= self.end else False
                        self.enqueue_wallet(
                            wallet,
                            i,
                            require_ownership=require,
                            trace_source=trace_source,
                        )
                elif r.tx_type == "Withdrawal" and r.amount < 0:
                    # A Bitaroo withdrawal is not itself a CGT event and must not
                    # seed a new wallet-history branch merely because the coins
                    # were received by a supplied wallet. The recipient wallet is
                    # traced when a target-year disposal actually depends on that
                    # wallet, or when a later Bitaroo Deposit from that wallet is
                    # part of an already-required exchange holding episode.
                    #
                    # This is deliberately NOT a blanket recipient-wallet enqueue:
                    # doing that causes unrelated historical wallet episodes to be
                    # backtraced for a target FY and can prompt for ancient source
                    # transactions that have no connection to a target-year CGT event.
                    continue
        self.bitaroo_queue.extend(remaining)

    def required_years_after(self, reports: "ReportSet") -> List[int]:
        # Do not request a year already covered. We intentionally return only
        # missing years discovered by the dependency graph.
        return sorted(y for y in self.required_fys if y not in reports.years)


    def reconcile_target_year_wallet_activity(
        self,
        trace_source: str = "Wallet Tracer",
    ) -> bool:
        """Activate target-year wallet sends after initial Bitaroo tracing.

        The Bitaroo statement contains exchange-side disposals, but wallet
        transactions can themselves be CGT events even when the BTC never reaches
        the taxpayer's Bitaroo account. For the auxiliary target-year scan, only
        outgoing wallet rows are direct dependencies: a send can represent a
        disposal, gift, external transfer, or network-fee disposal and therefore
        can lead to a target-year CGT event. An incoming wallet row alone does not
        create a CGT disposal for the recipient, so receives must not trigger a
        wallet ownership question or auxiliary trace. Later Bitaroo and wallet
        graph processing will still discover any relevant disposal that arises from
        BTC received during the target year. Wallets with no target-year sends are
        untouched.
        """
        changed = False
        for wallet_key, rows in list(self.wallets.items()):
            target_indices = [
                idx for idx, row in enumerate(rows)
                if self.start <= row.dt <= self.end and row.outgoing
            ]
            if not target_indices:
                continue

            wallet = wallet_key
            status = self.ownership.planning_wallet_status(
                wallet,
                require_ownership=(wallet != "Bitaroo"),
                trace_source=trace_source,
            )
            if wallet != "Bitaroo" and status != WalletOwnership.PERSONAL:
                continue

            for idx in target_indices:
                ref = self.wallets[wallet][idx].ref
                if ref not in self.required_wallet_rows:
                    self.enqueue_wallet(
                        wallet,
                        idx,
                        require_ownership=False,
                        trace_source=trace_source,
                    )
                    changed = True
        return changed


# ---------------------------------------------------------------------------
# Price book
# ---------------------------------------------------------------------------

class PriceBook:
    """BTC/AUD: observed trade at timestamp, daily close only without time."""
    YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-AUD"

    def __init__(self, trades):
        self.cache = {}
        self.source_for_day = {}  # keys are UTC datetimes for timed prices

    @staticmethod
    def _http_get(url, headers=None):
        return cached_http(url, headers).decode("utf-8", errors="replace")

    def _yahoo_close(self, dt: date) -> Optional[Decimal]:
        """Get BTC-AUD daily close from Yahoo Finance chart API."""
        try:
            start = int(datetime(dt.year, dt.month, dt.day, tzinfo=ZoneInfo("UTC")).timestamp())
            end = start + 3 * 86400
            params = urllib.parse.urlencode({
                "period1": str(start),
                "period2": str(end),
                "interval": "1d",
                "events": "history",
                "includeAdjustedClose": "true",
            })
            raw = self._http_get(self.YAHOO_URL + "?" + params, {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
            })
            data = json.loads(raw)
            result = (((data.get("chart") or {}).get("result") or [None])[0])
            if not result:
                return None
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [None])[0] or {}
            closes = quote.get("close") or []
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                d = datetime.fromtimestamp(int(ts), tz=ZoneInfo("UTC")).date()
                if d == dt:
                    value = Decimal(str(close))
                    if value.is_finite() and value > 0:
                        return value
        except Exception:
            return None
        return None

    def external_close(self, dt):
        if dt not in self.cache:
            p = self._yahoo_close(dt)
            source = "Yahoo Finance BTC-AUD daily close (date-only input)"
            if p is None:
                # Last observed trade before the end of this UTC price date.
                end = datetime(dt.year, dt.month, dt.day, tzinfo=UTC) + timedelta(days=1, microseconds=-1)
                p, source = INTRADAY.rate("BTC", end, announce=False)
                source += "; date-only UTC daily close fallback"
            self.cache[dt] = (p, source)
        self.source_for_day[dt] = self.cache[dt][1]
        return self.cache[dt]

    def ask(self, dt, context):
        if has_precise_time(dt):
            when = utc_time(dt)
            p, source = INTRADAY.rate("BTC", when, context=context)
            self.source_for_day[when] = source
        else:
            p, source = self.external_close(dt)
            price_result("BTC", dt, p, source)
        return p

# ---------------------------------------------------------------------------
# Foreign-exchange price book
# ---------------------------------------------------------------------------

class FxPriceBook:
    """Historical foreign-currency-to-AUD daily-close lookup.

    The lookup is deliberately fully dynamic. No historical exchange-rate
    values or date-specific FX data are embedded in the program.

    Automatic source priority:
      1. Yahoo Finance historical daily candle, using both query1 and query2
         hosts and both direct/inverse currency pairs.
      2. ExchangeRates.org.uk date-specific historical page for the requested
         calendar date.
      3. ExchangeRates.org.uk annual historical table for the requested year.
      4. X-Rates historical date page as an independent fallback.

    Every automatic result must correspond to the requested calendar date.
    The ExchangeRates.org.uk source is useful here because its historical
    calendar tables include weekend/holiday dates rather than forcing the
    program to invent a prior-business-day substitution.

    If automatic providers fail, the calculation stops with an actionable error.
    No price-entry prompt or daily fallback for timestamped records is used.
    """
    YAHOO_URLS = (
        "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
    )
    ERUK_DAILY_URL = "https://www.exchangerates.org.uk/historical/{currency}/{day}_{month}_{year}"
    ERUK_YEAR_URL = "https://www.exchangerates.org.uk/{currency}-AUD-spot-exchange-rates-history-{year}.html"
    XRATES_DAILY_URL = "https://www.x-rates.com/historical/?amount=1&date={iso_date}&from={currency}"

    RETRIES = 2
    TIMEOUT_SECONDS = 20

    def __init__(self):
        self.cache: Dict[Tuple[str, date], Tuple[Decimal, str]] = {}
        self.failures: Dict[Tuple[str, date], List[str]] = defaultdict(list)

    @classmethod
    def _http_get(cls, url):
        return cached_http(url, {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json"}).decode("utf-8", errors="replace")

    @staticmethod
    def _positive_rate(raw: object) -> Optional[Decimal]:
        try:
            value = Decimal(str(raw).replace(",", "").strip())
        except Exception:
            return None
        if not value.is_finite() or value <= 0:
            return None
        return value

    def _record_failure(self, key: Tuple[str, date], source: str, exc: Exception) -> None:
        detail = f"{source}: {type(exc).__name__}: {exc}"
        self.failures[key].append(detail[:400])

    def _yahoo_usd_leg(self, currency, year):
        """Daily USD value of one currency, indexed by provider trading date."""
        if not hasattr(self, '_usd_legs'):
            self._usd_legs = {}
        key = (currency, year)
        if key in self._usd_legs:
            return self._usd_legs[key]
        if currency == 'USD':
            return None  # identity handled by caller
        start = int(datetime(year - 1, 12, 25, tzinfo=UTC).timestamp())
        end = min(int(datetime(year + 1, 1, 3, tzinfo=UTC).timestamp()), int(datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()))
        # FX crosses are not all traded/listed. Obtain two independently quoted
        # USD legs, never assume that a constructed Yahoo ticker contains data.
        for ticker, inverse in ((f'{currency}USD=X', False), (f'USD{currency}=X', True), (f'{currency}=X', True)):
            for endpoint in self.YAHOO_URLS:
                try:
                    url = endpoint.format(ticker=urllib.parse.quote(ticker, safe='')) + '?' + urllib.parse.urlencode({'period1':start,'period2':end,'interval':'1d'})
                    data = json.loads(self._http_get(url))
                    result = data['chart']['result'][0]
                    meta = result.get('meta') or {}
                    expected_currency = currency if inverse else 'USD'
                    if meta.get('currency') != expected_currency:
                        raise ValueError('Yahoo returned an unexpected quote currency')
                    zone = ZoneInfo(meta.get('exchangeTimezoneName') or 'UTC')
                    values = ((result.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
                    prices = {}
                    for ts, raw in zip(result.get('timestamp') or [], values):
                        rate = self._positive_rate(raw)
                        if rate is None:
                            continue
                        observed = datetime.fromtimestamp(int(ts), UTC)
                        day = observed.astimezone(zone).date()
                        prices[day] = (Decimal('1') / rate if inverse else rate)
                    if prices:
                        self._usd_legs[key] = prices
                        return prices
                except (KeyError, IndexError, TypeError, ValueError, urllib.error.URLError, OSError) as exc:
                    self._record_failure((currency, date(year,1,1)), 'Yahoo USD daily leg', exc)
        self._usd_legs[key] = {}
        return {}

    def _yahoo_close(self, currency, dt):
        if currency == 'AUD':
            return Decimal('1'), 'AUD (no conversion)'
        base = self._yahoo_usd_leg(currency, dt.year)
        aud = self._yahoo_usd_leg('AUD', dt.year)
        if not aud or (currency != 'USD' and not base):
            return None
        days = set(aud) if currency == 'USD' else set(base).intersection(aud)
        days = [day for day in days if day <= dt and (dt - day).days <= 4]
        if not days:
            return None
        day = max(days)
        # A prior business close is permitted only for a weekend date. A missing
        # weekday could be missing data; do not silently substitute it.
        if day != dt and dt.weekday() < 5:
            return None
        rate = (Decimal('1') if currency == 'USD' else base[day]) / aud[day]
        note = '; previous business close for weekend' if day != dt else ''
        return rate, f'Yahoo Finance {currency}/AUD daily close via USD; provider trading date {day}{note}'

    @staticmethod
    def _html_table_lines(raw: str) -> List[str]:
        """Return HTML table rows as normalized text without breaking cells apart.

        V27 used a tag-stripping regex that treated closing ``</div>`` tags as
        row boundaries. ExchangeRates.org.uk nests the source/destination cells
        inside div/span elements, so that could split a single historical table
        row into several unrelated lines and make a valid RUB/AUD row invisible
        to the parser. Here we parse actual ``<tr>`` boundaries and retain all
        descendant text from the complete row.
        """

        class _TableRowParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.in_row = 0
                self.in_script_or_style = 0
                self.current: List[str] = []
                self.rows: List[str] = []

            def handle_starttag(self, tag: str, attrs) -> None:
                tag = tag.lower()
                if tag in {"script", "style"}:
                    self.in_script_or_style += 1
                    return
                if tag == "tr":
                    self.in_row += 1
                    if self.in_row == 1:
                        self.current = []

            def handle_endtag(self, tag: str) -> None:
                tag = tag.lower()
                if tag in {"script", "style"}:
                    if self.in_script_or_style:
                        self.in_script_or_style -= 1
                    return
                if tag == "tr" and self.in_row:
                    self.in_row -= 1
                    if self.in_row == 0:
                        line = re.sub(r"\s+", " ", " ".join(self.current)).strip()
                        if line:
                            self.rows.append(line)
                        self.current = []

            def handle_data(self, data: str) -> None:
                if self.in_row and not self.in_script_or_style:
                    self.current.append(data)

        parser = _TableRowParser()
        parser.feed(raw)
        parser.close()
        return parser.rows

    @staticmethod
    def _normalised_html_text(raw: str) -> str:
        """Plain text fallback for malformed/non-table ExchangeRates pages."""
        text = html_lib.unescape(raw)
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _exchange_rates_uk_pair_from_dated_page(
        self, raw: str, currency: str
    ) -> Optional[Decimal]:
        """Extract currency/AUD from the actual dated historical table row.

        The source currency label, destination label and requested pair
        expression must occur in the same structural row. This deliberately
        ignores unrelated site-wide converter widgets, which previously caused
        V26 to capture a generic converter value.
        """
        currency = currency.upper().strip()
        pair_pattern = re.compile(
            rf"1\s+{re.escape(currency)}\s+to\s+AUD\s*=\s*"
            rf"(?:A\$|AU\$|\$|€|£|¥|₽|₹)?\s*"
            rf"([0-9]+(?:\.[0-9]+)?)",
            flags=re.I,
        )
        source_label = re.compile(
            rf"{re.escape(currency)}.*Australian Dollar \(AUD\)"
            rf"|Australian Dollar \(AUD\).*{re.escape(currency)}",
            flags=re.I,
        )

        # Preferred path: actual HTML table rows. This survives nested div/span
        # wrappers because row boundaries come only from <tr> tags.
        for line in self._html_table_lines(raw):
            if not source_label.search(line):
                continue
            match = pair_pattern.search(line)
            if match:
                rate = self._positive_rate(match.group(1))
                if rate is not None:
                    return rate

        # Fallback: tolerate a malformed page with no usable <tr> elements by
        # searching the normalized document text, but require the destination
        # label to be close to the pair expression. This is intentionally
        # constrained so a generic converter widget cannot win.
        text = self._normalised_html_text(raw)
        close_pattern = re.compile(
            rf"{re.escape(currency)}\s*.{{0,300}}?"
            rf"Australian Dollar \(AUD\)\s*.{{0,300}}?"
            rf"1\s+{re.escape(currency)}\s+to\s+AUD\s*=\s*"
            rf"(?:A\$|AU\$|\$|€|£|¥|₽|₹)?\s*"
            rf"([0-9]+(?:\.[0-9]+)?)",
            flags=re.I,
        )
        match = close_pattern.search(text)
        if match:
            return self._positive_rate(match.group(1))
        return None

    def _exchange_rates_uk_close(self, currency: str, dt: date) -> Optional[Tuple[Decimal, str]]:
        currency = currency.upper().strip()
        if currency == "AUD":
            return Decimal("1"), "AUD (no conversion)"

        key = (currency, dt)
        url = self.ERUK_DAILY_URL.format(
            currency=urllib.parse.quote(currency, safe=""),
            day=f"{dt.day:02d}",
            month=f"{dt.month:02d}",
            year=f"{dt.year:04d}",
        )
        try:
            raw = self._http_get(url)
            rate = self._exchange_rates_uk_pair_from_dated_page(raw, currency)
            if rate is not None:
                return rate, f"ExchangeRates.org.uk {currency}/AUD historical daily close"
            raise ValueError(
                f"Could not find the {currency}/AUD row in the dated ExchangeRates.org.uk page"
            )
        except Exception as exc:
            self._record_failure(key, "ExchangeRates.org.uk date-specific page", exc)

        # Some versions of the site expose the same historical data in the
        # annual table even when the date-specific page is temporarily cached or
        # unavailable. Accept the annual table only when the requested date and
        # currency pair appear in the same structural row/local window.
        year_url = self.ERUK_YEAR_URL.format(
            currency=urllib.parse.quote(currency, safe=""),
            year=dt.year,
        )
        try:
            raw = self._http_get(year_url)
            lines = self._html_table_lines(raw)
            date_tokens = {
                f"{dt.day} {dt.strftime('%B %Y')}".lower(),
                dt.strftime("%d %B %Y").lower(),
                f"{dt.day} {dt.strftime('%b %Y')}".lower(),
                dt.strftime("%d %b %Y").lower(),
            }
            pair_pattern = re.compile(
                rf"(?:₽|\$|A\$|AU\$)?\s*1\s+{re.escape(currency)}\s*=\s*"
                rf"(?:A\$|AU\$|\$|AUD)?\s*"
                rf"([0-9]+(?:\.[0-9]+)?)",
                flags=re.I,
            )
            for line in lines:
                low = line.lower()
                if not any(token in low for token in date_tokens):
                    continue
                # The annual table rows are already scoped to the RUB-AUD (or
                # equivalent) page, and the actual row has the form:
                #   Sunday 3 September 2023 ₽1 RUB = $<rate> ...
                # Parse the rate from the same date row rather than searching a
                # broad text window for an unrelated conversion widget.
                match = pair_pattern.search(line)
                if match:
                    rate = self._positive_rate(match.group(1))
                    if rate is not None:
                        return rate, f"ExchangeRates.org.uk {currency}/AUD annual historical daily close"
            raise ValueError(
                f"Could not find the {currency}/AUD historical row for {human_date(dt)} in annual table"
            )
        except Exception as exc:
            self._record_failure(key, "ExchangeRates.org.uk annual historical page", exc)

        return None

    def _xrates_close(self, currency: str, dt: date) -> Optional[Tuple[Decimal, str]]:
        """Fetch the requested day from the X-Rates historical table.

        X-Rates exposes one historical table for the requested base currency.
        The Australian Dollar row contains the AUD value of 1 unit of that base
        currency, followed by the inverse quotation. Only that row is accepted.
        """
        currency = currency.upper().strip()
        if currency == "AUD":
            return Decimal("1"), "AUD (no conversion)"

        key = (currency, dt)
        url = self.XRATES_DAILY_URL.format(
            iso_date=dt.isoformat(),
            currency=urllib.parse.quote(currency, safe=""),
        )
        try:
            raw = self._http_get(url)
            text = self._normalised_html_text(raw)
            expected_dates = {
                f"{dt.strftime('%b')} {dt.day}, {dt.year}",
                dt.strftime("%b %d, %Y"),
                f"{dt.strftime('%B')} {dt.day}, {dt.year}",
                dt.strftime("%B %d, %Y"),
            }
            if not any(marker in text for marker in expected_dates):
                raise ValueError(
                    f"X-Rates page did not identify the requested date {human_date(dt)}"
                )

            lines = self._html_table_lines(raw)

            # Typical rendered row:
            #   Australian Dollar <AUD-per-base> <inverse-rate>
            row_pattern = re.compile(
                r"^Australian Dollar\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"[0-9]+(?:\.[0-9]+)?(?:\s|$)",
                flags=re.I,
            )
            for line in lines:
                if not re.search(r"\bAustralian Dollar\b", line, flags=re.I):
                    continue
                match = row_pattern.search(line.strip())
                if not match:
                    # Minor markup/whitespace variation fallback.
                    match = re.search(
                        r"\bAustralian Dollar\b[^0-9]{0,40}"
                        r"([0-9]+(?:\.[0-9]+)?)\s+"
                        r"[0-9]+(?:\.[0-9]+)?",
                        line,
                        flags=re.I,
                    )
                if match:
                    rate = self._positive_rate(match.group(1))
                    if rate is not None:
                        return rate, f"X-Rates {currency}/AUD historical daily rate"

            # Constrained fallback for a malformed page with no <tr> rows.
            match = re.search(
                r"\bAustralian Dollar\b[^0-9]{0,60}"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"[0-9]+(?:\.[0-9]+)?",
                text,
                flags=re.I,
            )
            if match:
                rate = self._positive_rate(match.group(1))
                if rate is not None:
                    return rate, f"X-Rates {currency}/AUD historical daily rate"

            raise ValueError(
                "Could not find the Australian Dollar row in the X-Rates historical page"
            )
        except Exception as exc:
            self._record_failure(key, "X-Rates historical date-specific page", exc)
            return None

    def close_to_aud(self, currency: str, dt: date) -> Tuple[Decimal, str]:
        currency = currency.upper().strip() or "AUD"
        if currency != "AUD" and isinstance(dt, (TransactionDate, datetime)):
            return INTRADAY.rate(currency, utc_time(dt))
        key = (currency, dt)
        if key in self.cache:
            result = self.cache[key]
            if currency != "AUD":
                price_result(currency, dt, result[0], result[1])
            return result

        if currency == "AUD":
            result = (Decimal("1"), "AUD (no conversion)")
            self.cache[key] = result
            return result

        result = self._yahoo_close(currency, dt)
        if result is None:
            result = self._exchange_rates_uk_close(currency, dt)
        if result is None:
            result = self._xrates_close(currency, dt)

        if result is None:
            raise RuntimeError(
                f"Unable to obtain a historical {currency}/AUD daily closing exchange rate for "
                f"{human_date(dt)}. Automatic date-only sources were exhausted. "
                f"No rate was assumed or invented."
            )

        self.cache[key] = result
        price_result(currency, dt, result[0], result[1])
        return result


# Timestamped valuations. Daily providers above are used ONLY for date-only data.
class IntradayPrices:
    """Observed prices at/before a requested UTC instant; never a later candle.

    Public Kraken BTC/AUD trades, BTC Markets BTC/AUD minute trades and
    Forexite minute FX archives need no key. Bitcoin valuations use AUD pairs
    directly; no BTC/USD or foreign-exchange bridge is used for BTC prices.
    FX weekend closures use Friday's last available quote with disclosed age.
    Optional CoinAPI and verified CSV quotes cover unavailable pairs/times.
    """
    def __init__(self):
        self.cache = {}
        self.imported = {}
        self.failures = {}
        self.kraken_windows = []
        self.btcmarkets_windows = {}
        self.fx_archives = {}
        self.fx_quotes = {}

    def _validate(self, currency, when, observed, rate, source):
        age = (when - observed).total_seconds()
        rate = D(rate)
        if not rate.is_finite() or rate <= 0:
            raise ValueError(f"Invalid {currency}/AUD rate")
        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:
            raise ValueError(f"{source}: quote is future-dated or too old ({age:.3f}s)")
        return rate, f"{source}; observed {observed.isoformat().replace('+00:00', 'Z')}; age {age:.3f}s"

    def _kraken(self, when, lookback=None):
        windows = self.kraken_windows
        label = 'BTC/AUD'
        for start, end, trades in windows:
            if start <= when <= end:
                past = [(datetime.fromtimestamp(float(t[2]), UTC), D(t[0])) for t in trades if float(t[2]) <= when.timestamp()]
                if past:
                    observed, price = max(past, key=lambda x: x[0])
                    if (when - observed).total_seconds() <= QUOTE_MAX_AGE_SECONDS:
                        return self._validate('BTC', when, observed, price, f'Kraken {label} last observed trade')
        # Start with one minute; widen only when no preceding trade is found.
        if lookback is None:
            lookback = min(60, QUOTE_MAX_AGE_SECONDS)
        start = when - timedelta(seconds=lookback)
        cursor = str(int(start.timestamp() * 1_000_000_000))
        best = None
        # AUD market normally needs one page; cap protects against API loops.
        for _ in range(12):
            url = 'https://api.kraken.com/0/public/Trades?' + urllib.parse.urlencode({
                'pair': 'XBTAUD', 'since': cursor, 'count': 1000})
            for retry in range(3):
                data = json.loads(cached_http(url))
                if not data.get('error'):
                    break
                if not any('Too many requests' in e or 'Rate limit' in e for e in data['error']) or retry == 2:
                    raise ValueError('Kraken: ' + ', '.join(data['error']))
            if data.get('error'):
                raise ValueError('Kraken: ' + ', '.join(data['error']))
            result = data.get('result') or {}
            trades = next((v for k, v in result.items() if k != 'last' and isinstance(v, list)), [])
            if not trades:
                break
            page_start = datetime.fromtimestamp(int(cursor) / 1_000_000_000, UTC)
            page_end = max(datetime.fromtimestamp(float(t[2]), UTC) for t in trades)
            windows.append((page_start, page_end, trades))
            crossed = False
            for trade in trades:
                observed = datetime.fromtimestamp(float(trade[2]), UTC)
                if observed <= when:
                    if best is None or observed >= best[0]:
                        best = (observed, D(trade[0]))
                else:
                    crossed = True
            if crossed:
                break
            new_cursor = str(result.get('last', ''))
            if not new_cursor or new_cursor == cursor:
                raise ValueError('Kraken pagination did not advance')
            cursor = new_cursor
        else:
            raise ValueError('Kraken pagination limit reached before valuation time')
        if best is None:
            if lookback < QUOTE_MAX_AGE_SECONDS:
                return self._kraken(when, QUOTE_MAX_AGE_SECONDS)
            raise ValueError(f'Kraken has no sufficiently recent {label} trade before the transaction')
        return self._validate('BTC', when, best[0], best[1], f'Kraken {label} last observed trade')

    @staticmethod
    def _fx_weekend(when):
        """Forexite's documented Friday 23:00 to Sunday 23:00 CET/CEST closure.

        This exception is for FX only. A missing weekday price, an earlier
        week's archive, or a missing Friday archive is never carried forward.
        """
        local = when.astimezone(ZoneInfo('Europe/Berlin'))
        friday = (local - timedelta(days=(local.weekday() - 4) % 7)).date()
        close = datetime.combine(friday, datetime.min.time()).replace(hour=23, tzinfo=local.tzinfo)
        reopen = close + timedelta(days=2)
        return friday if close <= local < reopen else None

    def _fx_archive(self, day):
        if day in self.fx_archives:
            return self.fx_archives[day]
        url = 'https://www.forexite.com/free_forex_quotes/' + day.strftime('%Y/%m/%d%m%y.zip')
        body = cached_http(url)
        pairs = defaultdict(list)
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                entries = [info for info in archive.infolist() if info.filename.lower().endswith('.txt')]
                if not entries or sum(info.file_size for info in entries) > 100_000_000:
                    raise ValueError('Unexpected Forexite archive contents or size')
                for info in entries:
                    with archive.open(info) as file:
                        for row in csv.reader(io.TextIOWrapper(file, encoding='utf-8-sig')):
                            if len(row) == 7 and re.fullmatch(r'[A-Z]{6}', row[0]):
                                pairs[row[0]].append(row[1:])
        except zipfile.BadZipFile as exc:
            raise ValueError('Forexite did not return a quote archive') from exc
        if not pairs:
            raise ValueError('Forexite archive has no quote data')
        self.fx_archives[day] = pairs
        return pairs

    def _fx_pair_quote(self, pair, when):
        """Last fully available minute close, with no use of the live minute.

        The provider specifies CET/CEST but does not supply tick timestamps in
        this format. Allow one full minute after the bar label before using its
        close; disclose this conservative availability bound, not a fictitious
        exact tick observation. Never use weekend archive rows that may repeat
        Friday's quote with a new label.
        """
        friday = self._fx_weekend(when)
        local_day = when.astimezone(ZoneInfo('Europe/Berlin')).date()
        days = [friday] if friday else [local_day, local_day - timedelta(days=1)]
        errors = []
        for day in days:
            try:
                archive = self._fx_archive(day)
                key = (day, pair)
                if key not in self.fx_quotes:
                    quotes = []
                    for row in archive.get(pair, []):
                        naive = datetime.strptime(row[0] + row[1], '%Y%m%d%H%M%S')
                        zone = ZoneInfo('Europe/Berlin')
                        local = naive.replace(tzinfo=zone)
                        # Ambiguous/nonexistent local minutes cannot establish a UTC quote.
                        if local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset():
                            continue
                        label = local.astimezone(UTC)
                        if label.astimezone(zone).replace(tzinfo=None) != naive:
                            continue
                        opening, high, low, close = map(D, row[2:])
                        if not all(v.is_finite() and v > 0 for v in (opening, high, low, close)) or not low <= min(opening, close) <= max(opening, close) <= high:
                            raise ValueError('Invalid Forexite OHLC quote')
                        quotes.append((label + timedelta(minutes=1), close, label))
                    self.fx_quotes[key] = sorted(quotes)
                quotes = self.fx_quotes[key]
                if friday:
                    zone = ZoneInfo('Europe/Berlin')
                    closure = datetime.combine(friday, datetime.min.time()).replace(hour=23, tzinfo=zone).astimezone(UTC)
                    quotes = [q for q in quotes if q[2].astimezone(zone).date() == friday and q[0] <= closure]
                    if not quotes:
                        raise ValueError('No verified quote from the immediately preceding Friday')
                index = bisect.bisect_right([q[0] for q in quotes], when) - 1
                if index < 0:
                    raise ValueError(f'No past Forexite {pair} minute quote')
                available, price, label = quotes[index]
                age = (when - available).total_seconds()
                if friday:
                    if label.astimezone(ZoneInfo('Europe/Berlin')).date() != friday or not 0 <= age <= 72 * 3600:
                        raise ValueError('No verified quote from the immediately preceding Friday')
                elif age < 0 or (when - (label - timedelta(minutes=1))).total_seconds() > QUOTE_MAX_AGE_SECONDS:
                    raise ValueError(f'Forexite {pair} weekday quote too old (minimum age {age:.3f}s)')
                source = (f'Forexite {pair[:3]}/{pair[3:]} one-minute close; '
                          f'bar label {label.isoformat().replace("+00:00", "Z")}; '
                          f'available by {available.isoformat().replace("+00:00", "Z")}; '
                          'tick timestamp unavailable; ' + ('last pre-closure quote; ' if friday else '') +
                          f'age {age:.3f}s; minimum age from conservative availability bound')
                return available, price, source
            except (ValueError, KeyError, TypeError, urllib.error.URLError, OSError) as exc:
                errors.append(str(exc))
        raise ValueError('; '.join(errors))

    def _moex_fx(self, instrument, when):
        """Public MOEX spot currency minutes; never substitute futures/fixings.

        No old feed is carried across a discontinued market. A weekend request
        must find quotes on the immediately preceding Friday itself.
        """
        zone = ZoneInfo('Europe/Moscow')
        friday = self._fx_weekend(when)
        day = friday or when.astimezone(zone).date()
        key = ('MOEX', day, instrument)
        if key not in self.fx_quotes:
            quotes = []
            start = 0
            for _ in range(10):
                url = ('https://iss.moex.com/iss/engines/currency/markets/selt/boards/CETS/securities/'
                       + urllib.parse.quote(instrument, safe='') + '/candles.json?' +
                       urllib.parse.urlencode({'from': day.isoformat(), 'till': day.isoformat(),
                                               'interval': 1, 'iss.meta': 'off', 'start': start}))
                table = json.loads(cached_http(url), parse_float=Decimal)['candles']
                rows = table['data']
                columns = table['columns']
                for values in rows:
                    row = dict(zip(columns, values))
                    label = datetime.fromisoformat(row['begin']).replace(tzinfo=zone).astimezone(UTC)
                    available = datetime.fromisoformat(row['end']).replace(tzinfo=zone).astimezone(UTC) + timedelta(seconds=1)
                    opening, high, low, price = (D(row[k]) for k in ('open', 'high', 'low', 'close'))
                    valid_prices = all(v.is_finite() and v > 0 for v in (opening, high, low, price))
                    if not valid_prices or not low <= min(opening, price) <= max(opening, price) <= high or not 0 < (available - label).total_seconds() <= 60:
                        raise ValueError('Invalid MOEX minute quote')
                    quotes.append((available, price, label))
                if len(rows) < 500:
                    break
                start += len(rows)
            else:
                raise ValueError('MOEX minute pagination limit reached')
            self.fx_quotes[key] = sorted(quotes)
        quotes = self.fx_quotes[key]
        index = bisect.bisect_right([q[0] for q in quotes], when) - 1
        if index < 0:
            raise ValueError('No MOEX spot quote on requested trading day; market may be unavailable')
        available, price, label = quotes[index]
        age = (when - available).total_seconds()
        if friday:
            if label.astimezone(zone).date() != friday or not 0 <= age <= 72 * 3600:
                raise ValueError('MOEX has no verified quote from the immediately preceding Friday')
        elif age < 0 or (when - label).total_seconds() > QUOTE_MAX_AGE_SECONDS:
            raise ValueError(f'MOEX weekday quote too old (minimum age {age:.3f}s)')
        source = (f'MOEX {instrument} one-minute close; '
                  f'bar label {label.isoformat().replace("+00:00", "Z")}; '
                  f'available by {available.isoformat().replace("+00:00", "Z")}; '
                  'tick timestamp unavailable; ' + ('last pre-closure quote; ' if friday else '') +
                  f'age {age:.3f}s; minimum age from minute availability bound')
        return available, price, source

    def _forexite(self, currency, when):
        if not re.fullmatch(r'[A-Z]{3}', currency):
            raise ValueError('FX source requires a three-letter currency code')
        friday = self._fx_weekend(when)
        day = friday or when.astimezone(ZoneInfo('Europe/Berlin')).date()
        try:
            pairs = self._fx_archive(day)
        except (ValueError, urllib.error.URLError, OSError):
            if friday:
                raise
            pairs = self._fx_archive(day - timedelta(days=1))
        # Pair availability comes from the returned archive, not a user/exchange map.
        def leg(base, quote):
            if base == quote:
                return when, Decimal('1'), f'{base}/{quote} identity'
            direct, inverse = base + quote, quote + base
            pair = direct if direct in pairs else inverse
            if pair not in pairs:
                # Public source instrument identifiers, not taxpayer/exchange cases.
                moex_pairs = {('USD', 'RUB'): 'USD000UTSTOM'}
                reverse = (quote, base) in moex_pairs
                instrument = moex_pairs.get((base, quote)) or moex_pairs.get((quote, base))
                if not instrument:
                    raise ValueError(f'Public sources have no {base}/{quote} quotes')
                observed, value, source = self._moex_fx(instrument, when)
                return observed, Decimal('1') / value if reverse else value, source
            observed, value, source = self._fx_pair_quote(pair, when)
            return observed, value if pair == direct else Decimal('1') / value, source
        if currency + 'AUD' in pairs or 'AUD' + currency in pairs:
            _, value, source = leg(currency, 'AUD')
            return value, source
        first_time, first, first_source = leg(currency, 'USD')
        second_time, second, second_source = leg('USD', 'AUD')
        age = (when - min(first_time, second_time)).total_seconds()
        return first * second, (f'FX cross {currency}/USD x USD/AUD; age {age:.3f}s; '
                                f'minute quote; {first_source}; {second_source}')

    def _btcmarkets(self, when):
        """Direct BTC/AUD trading prices, with completed-minute precision.

        The public archive supplies minute OHLCV, not individual tick times.
        Exclude the minute containing the transaction, zero-volume filler bars,
        and bars too old even at their earliest possible trade time. No FX or
        weekend closure exception is used. Hour-aligned windows are reused.
        """
        cutoff = when - timedelta(seconds=QUOTE_MAX_AGE_SECONDS)
        earliest = cutoff.replace(minute=0, second=0, microsecond=0)
        end = when.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while end > earliest:
            # At most 720 minute bars per request, below the provider's 1000 cap.
            start = max(earliest, end - timedelta(hours=12))
            key = (start, end)
            if key not in self.btcmarkets_windows:
                url = 'https://api.btcmarkets.net/v3/markets/BTC-AUD/candles?' + urllib.parse.urlencode({
                    'timeWindow': '1m', 'from': start.isoformat().replace('+00:00', 'Z'),
                    'to': end.isoformat().replace('+00:00', 'Z')})
                data = json.loads(cached_http(url), parse_float=Decimal)
                if not isinstance(data, list) or len(data) > 1000:
                    raise ValueError('BTC Markets did not return a bounded candle list')
                quotes = []
                for row in data:
                    if not isinstance(row, list) or len(row) != 6:
                        raise ValueError('Invalid BTC Markets candle structure')
                    label = datetime.fromisoformat(row[0].replace('Z', '+00:00'))
                    if label.tzinfo is None:
                        raise ValueError('BTC Markets candle lacks timezone')
                    label = label.astimezone(UTC)
                    if label.second or label.microsecond or not start <= label <= end:
                        raise ValueError('BTC Markets candle is not within the requested UTC minute window')
                    volume = D(row[5])
                    if not volume.is_finite() or volume < 0:
                        raise ValueError('Invalid BTC Markets candle volume')
                    if volume == 0:
                        continue
                    opening, high, low, close = map(D, row[1:5])
                    if not all(v.is_finite() and v > 0 for v in (opening, high, low, close)) or not low <= min(opening, close) <= max(opening, close) <= high:
                        raise ValueError('Invalid BTC Markets OHLC trade prices')
                    quotes.append((label, close))
                self.btcmarkets_windows[key] = sorted(quotes)
            candidates = [(label, price) for label, price in self.btcmarkets_windows[key]
                          if cutoff <= label and label + timedelta(minutes=1) <= when]
            if candidates:
                label, price = max(candidates, key=lambda q: q[0])
                available = label + timedelta(minutes=1)
                age = (when - available).total_seconds()
                source = ('BTC Markets BTC/AUD one-minute trade close; '
                          f'bar start {label.isoformat().replace("+00:00", "Z")}; '
                          f'available by {available.isoformat().replace("+00:00", "Z")}; '
                          f'tick timestamp unavailable; age {age:.3f}s; '
                          'minimum age from completed-minute availability bound')
                return price, source
            end = start
        raise ValueError('BTC Markets has no sufficiently recent completed BTC/AUD trading minute before the transaction')

    def _coinapi(self, currency, when):
        key = os.environ.get('COINAPI_KEY', '')
        if not key:
            raise ValueError('No COINAPI_KEY configured for historical intraday FX')
        url = ('https://api-historical.exrates.coinapi.io/v1/exchangerate/'
               + urllib.parse.quote(currency, safe='') + '/AUD?'
               + urllib.parse.urlencode({'time': when.isoformat().replace('+00:00', 'Z')}))
        data = json.loads(cached_http(url, {'X-CoinAPI-Key': key, 'Accept': 'application/json'}))
        if data.get('asset_id_base') != currency or data.get('asset_id_quote') != 'AUD':
            raise ValueError('CoinAPI response asset pair does not match request')
        observed = datetime.fromisoformat(data['time'].replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('CoinAPI rate timestamp lacks timezone')
        return self._validate(currency, when, observed.astimezone(UTC), data['rate'], f'CoinAPI historical {currency}/AUD rate')

    def rate(self, currency, when, context="", *, announce=True):
        currency = currency.upper()
        when = utc_time(when, require=True)
        key = (currency, when)
        if key in self.cache:
            result = self.cache[key]
            if announce:
                price_result(currency, when, result[0], result[1])
            return result
        if key in self.imported:
            observed, rate, source = self.imported[key]
            result = self._validate(currency, when, observed, rate, 'Imported verified quote: ' + source)
        else:
            errors = []
            providers = [lambda: self._kraken(when), lambda: self._btcmarkets(when), lambda: self._coinapi(currency, when)] if currency == 'BTC' else [lambda: self._forexite(currency, when), lambda: self._coinapi(currency, when)]
            for provider in providers:
                try:
                    result = provider()
                    break
                except (ValueError, KeyError, TypeError, urllib.error.URLError, OSError) as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
            else:
                raise RuntimeError(f'No transaction-time {currency}/AUD value for {display_time(when)}. '
                                   + '; '.join(errors) + '. Supply --quotes or a supported historical API key. Daily closes are reserved for date-only inputs.')
        self.cache[key] = result
        if announce:
            price_result(currency, when, result[0], result[1])
        return result


INTRADAY = IntradayPrices()
CPI_VALUES = {}


def minimum_tax_gap(minimum_gain, basic_tax, basic_tax_without_gain, deductible_gifts=Decimal('0'), deductible_conservation=Decimal('0'), exempt_payment=False):
    """Division 119 gap, using supplied whole-return basic tax liabilities.

    Personal brackets/offsets are intentionally not inferred from a Bitcoin-only
    ledger. Section 119-15 payment exemptions must be established by the user.
    """
    values = [D(x) for x in (minimum_gain,basic_tax,basic_tax_without_gain,deductible_gifts,deductible_conservation)]
    if any(not x.is_finite() or x < 0 for x in values):
        raise ValueError('Minimum tax inputs must be finite non-negative amounts')
    gain, tax, without, gifts, conservation = values
    if without > tax:
        raise ValueError('Basic tax without minimum-tax gains cannot exceed basic tax with those gains')
    if not isinstance(exempt_payment, bool):
        raise ValueError('exempt_payment must be a JSON boolean')
    if exempt_payment:
        return Decimal('0')
    base = max(Decimal('0'), gain - gifts - conservation)
    gap = base * Decimal('.30') - (tax - without)
    return max(Decimal('0'), gap.to_integral_value(rounding=ROUND_FLOOR))


TAX_CONTEXT = None
CARRIED_LOSSES = Decimal('0')

def annual_summary(events, carried_losses=Decimal('0')):
    """Apply losses BEFORE discount. Bitcoin is non-residential.

    After 1 July 2027 deferred gains are reduced before post-reform gains
    (s102-5). Before reform use losses on undiscounted gains first.
    Does not include other assets, prior losses unless supplied, or personal tax.
    """
    def gross_parts(event):
        if event.gross_gain or event.gross_loss:
            return event.gross_gain, event.gross_loss
        return max(event.gain_loss, Decimal('0')), max(-event.gain_loss, Decimal('0'))
    current_losses = sum((gross_parts(e)[1] for e in events), Decimal('0'))
    prior_losses = D(carried_losses)
    losses = prior_losses + current_losses
    groups = defaultdict(lambda: Decimal('0'))
    for e in events:
        gross_gain, _loss = gross_parts(e)
        if gross_gain <= 0:
            continue
        discount = bool(e.discounted_gain > 0)
        if e.bucket.startswith('pre-reform deferred'):
            priority = 0
        elif e.bucket == 'post-reform':
            priority = 2
        else:
            priority = 1 if discount else 0
        groups[(priority, discount, e.bucket == 'post-reform', e.bucket)] += gross_gain
    total = Decimal('0')
    minimum = Decimal('0')
    before_discount = Decimal('0')
    discounted_long_gain = Decimal('0')
    for (priority, discount, post, bucket), gain in sorted(groups.items()):
        used = min(losses, gain)
        losses -= used
        remaining = gain - used
        taxable = remaining * (Decimal('.5') if discount else Decimal('1'))
        total += taxable
        before_discount += remaining
        if bucket == 'held >= 12 months':
            discounted_long_gain += taxable
        if post:
            minimum += taxable
    return {'net_gain': total, 'net_loss': losses, 'minimum_tax_gain': minimum,
            'net_gain_before_discount': before_discount,
            # Signed reporting results retain the unapplied loss; the tax
            # net_gain above remains non-negative and is not a negative tax offset.
            'net_result_before_discount': before_discount - losses,
            'net_result_after_discount': total - losses,
            'discounted_long_gain': discounted_long_gain,
            'current_year_losses': current_losses, 'prior_year_losses': prior_losses,
            'losses_applied': current_losses + prior_losses - losses}


# ---------------------------------------------------------------------------
# CGT engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, taxpayer: str, fy_start: int, wallets: Dict[str, List[WalletTx]], reports: ReportSet,
                 active_wallet_rows: Optional[set[Tuple[str, int]]] = None,
                 active_bitaroo_rows: Optional[set[int]] = None, ownership: Optional[WalletOwnership] = None,
                 other_exchange_rows: Optional[List[OtherExchangeRow]] = None,
                 primary_wallet_rows: Optional[set[Tuple[str, int]]] = None,
                 primary_bitaroo_rows: Optional[set[int]] = None):
        self.taxpayer = taxpayer
        self.fy_start = fy_start
        self.period_start, self.period_end = fy_bounds(fy_start)
        self.wallets = wallets
        self.reports = reports
        self.ownership = ownership or WalletOwnership(taxpayer)
        self.active_wallet_rows = active_wallet_rows
        self.primary_wallet_rows = primary_wallet_rows
        self.primary_bitaroo_rows = primary_bitaroo_rows
        self.active_bitaroo_rows = active_bitaroo_rows
        self.price = PriceBook(reports.trades)
        self.price_usage = []
        self._price_usage_keys = set()
        self.holdings: Dict[str, List[Lot]] = defaultdict(list)
        self.events: List[EventComponent] = []
        self.review: List[str] = []
        self.processed_wallet: set[Tuple[str, int]] = set()
        self.claimed_wallet_fee_txids: set[str] = set()
        self.validated_wallet: set[Tuple[str, int]] = set()
        self.other_exchange_rows: List[OtherExchangeRow] = other_exchange_rows or []
        self.fx = FxPriceBook()
        self.fx_usage: List[Tuple[date, str, Decimal, Decimal, str]] = []
        self._exchange_values = {}
        self.other_exchange_wallets: Dict[Tuple[str, int], str] = {}  # inferred runtime matches only
        self.seeded_other_exchange_rows: set[Tuple[str, int]] = set()
        self.matched_other_exchange_deposits: set[Tuple[str, int]] = set()
        self.matched_other_exchange_wallet_receipts: set[Tuple[str, int]] = set()
        self.matched_other_exchange_wallet_buys: set[Tuple[str, int]] = set()
        self.other_exchange_match_notes: List[str] = []
        # Bitaroo withdrawals and wallet recipient rows are separate ledger
        # events because the exchange statement is UTC while Bitcoin Core wallet CSVs record
        # the recipient wallet posting date/order.
        self.pending_withdrawals: Dict[Tuple[str, int, int], List[Lot]] = {}
        self.unreconciled_bitaroo_deposits: set[int] = set()
        self.unreconciled_disposals: set[Tuple[date, str, str]] = set()
        self.lot_seq = 0
        self.transition_price: Optional[Decimal] = None
        self.transition_applied = False
        self._trade_lookup = self._make_trade_lookup()

    def _make_trade_lookup(self):
        # Keyed by (dt, type), with a queue so same-second repeated sales/buys
        # are not overwritten by a dict.
        q: Dict[Tuple[datetime, str], List[Trade]] = defaultdict(list)
        for t in self.reports.trades:
            typ = "buy" if t.side == "buy" else "sell"
            q[(t.dt, typ)].append(t)
        return q

    def new_lot(self, acquired: date, qty: Decimal, cost: Decimal, source: str, trace: str = "",
                basis_known: bool = True, deferred_market_price: bool = False,
                source_ownership_unresolved: bool = False,
                source_wallet_ref: Optional[Tuple[str, int]] = None,
                deferred_wallet_ownership: Tuple[Tuple[str, date], ...] = ()) -> Lot:
        self.lot_seq += 1
        return Lot(f"L{self.lot_seq:06d}", acquired, qty, cost, source, trace=trace,
                   basis_known=basis_known, deferred_market_price=deferred_market_price,
                   source_ownership_unresolved=source_ownership_unresolved,
                   source_wallet_ref=source_wallet_ref,
                   deferred_wallet_ownership=deferred_wallet_ownership)

    def qty(self, wallet: str) -> Decimal:
        return sum((l.qty for l in self.holdings.get(wallet, [])), Decimal("0"))

    def add(self, wallet: str, lot: Lot):
        if lot.qty > BTC_EPS:
            self.holdings[wallet].append(lot)

    def _take(self, wallet: str, qty: Decimal, reason: str, dt: date, allow_short: bool = False) -> List[Lot]:
        lots = list(self.holdings.get(wallet, []))
        total = sum((l.qty for l in lots), Decimal("0"))
        if qty <= BTC_EPS:
            return []
        shortfall = max(Decimal("0"), qty - total)
        if shortfall > BTC_EPS and dt >= self.period_start:
            # Retain full disposal quantity, separating the unknown cost basis.
            # Otherwise full sale proceeds get incorrectly assigned to fewer BTC.
            unknown = self.new_lot(dt, shortfall, Decimal("0"), "Missing source quantity", basis_known=False)
            self.holdings[wallet] = []
            self.review.append(f"{human_date(dt)} {wallet}: {qb(shortfall)} BTC source quantity is missing; related proceeds excluded pending reconciliation.")
            return lots + [unknown]
        if total + BTC_EPS < qty:
            # Historical backtracing is constrained by the supplied Bitaroo/wallet
            # history floor. If an old statement row says the transfer/sale happened,
            # do not turn the missing pre-floor portion into a target-year review item.
            # The untraced portion simply has no reconstructed cost basis. Any shortfall
            # that actually affects a target-year disposal is surfaced when that target
            # disposal itself is processed.
            if dt >= self.period_start:
                self.review.append(f"{human_date(dt)} {reason}: needed {qb(qty)} BTC from {wallet}, but only {qb(total)} BTC is traced.")
            if not allow_short:
                qty = min(qty, total)
        if total <= BTC_EPS:
            return []

        qty = min(qty, total)
        fraction = qty / total
        moved: List[Lot] = []
        kept: List[Lot] = []
        for lot in lots:
            take = lot.qty * fraction
            if take <= BTC_EPS:
                kept.append(lot)
                continue
            moved.append(Lot(lot.id + "*", lot.acquired, take, lot.cost * fraction, lot.source,
                             lot.transition_mv * fraction if lot.transition_mv is not None else None,
                             lot.transition_gain * fraction if lot.transition_gain is not None else None,
                             lot.trace, lot.basis_known, lot.deferred_market_price,
                             lot.source_ownership_unresolved, lot.source_wallet_ref,
                             lot.deferred_wallet_ownership))
            left_qty = lot.qty - take
            if left_qty > BTC_EPS:
                left_frac = Decimal("1") - fraction
                kept.append(Lot(lot.id, lot.acquired, left_qty, lot.cost * left_frac, lot.source,
                                lot.transition_mv * left_frac if lot.transition_mv is not None else None,
                                lot.transition_gain * left_frac if lot.transition_gain is not None else None,
                                lot.trace, lot.basis_known, lot.deferred_market_price,
                                lot.source_ownership_unresolved, lot.source_wallet_ref,
                                lot.deferred_wallet_ownership))
        self.holdings[wallet] = kept
        return moved


    def _resolve_deferred_wallet_ownership(self, lot: Lot) -> None:
        """Resolve only the historical ODS wallets actually represented by a consumed lot."""
        if not lot.deferred_wallet_ownership:
            return

        # Newest dependency is the first possible ownership boundary.  Once the
        # taxpayer answers N, the parcel is an external acquisition from that
        # point and no older wallet in the historical chain needs to be examined.
        deps = list(lot.deferred_wallet_ownership)
        for wallet, boundary_dt in reversed(deps):
            status = self.ownership.planning_wallet_status(wallet, require_ownership=True)
            if status == WalletOwnership.PERSONAL:
                continue

            # The source wallet is not the taxpayer's.  The receiving taxpayer gets
            # the BTC at the market value on the transfer/boundary date, and older
            # provenance behind that external wallet is irrelevant.
            lot.acquired = boundary_dt
            lot.cost = Decimal("0")
            lot.source = f"External acquisition from {wallet}"
            lot.trace = (
                f"{human_date(boundary_dt)} external receipt {qb(lot.qty)} BTC from "
                f"{wallet}; market value deferred until target-FY disposal"
            )
            lot.deferred_market_price = True
            # A confirmed external wallet is a complete ownership boundary for
            # this parcel. Any unresolved sender/source provenance behind the
            # boundary belongs to that other person's history and must never be
            # chased merely because this parcel is later disposed of by the
            # taxpayer.
            lot.source_ownership_unresolved = False
            lot.source_wallet_ref = None
            lot.deferred_wallet_ownership = ()
            return

        lot.deferred_wallet_ownership = ()

    def _resolve_deferred_source_ownership(self, lot: Lot) -> None:
        """Ask source ownership only when an unresolved historical ODS lot is actually disposed.

        Historical replay may need to carry ancient ODS parcels through wallets and
        Bitaroo without deciding who owned their original source. The normal source
        ownership question is deliberately delayed until this specific lot reaches
        a disposal in the requested Financial Year.
        """
        if not lot.source_ownership_unresolved:
            return
        if lot.source_wallet_ref is None:
            lot.source_ownership_unresolved = False
            return
        wallet, row_num = lot.source_wallet_ref
        rows = self.wallets.get(wallet, [])
        source_row = next((w for w in rows if w.row_num == row_num), None)
        if source_row is None:
            lot.source_ownership_unresolved = False
            return

        self.ownership.decide_incoming_source(source_row)
        lot.source_ownership_unresolved = False

    def _resolve_deferred_market_price(self, lot: Lot, wallet: str, dt: date) -> None:
        """Resolve a deferred historical acquisition price only for a target-FY disposal.

        Historical disposals are replayed to reconstruct the target-year holdings, but
        their own CGT calculations are outside the requested Financial Year. A deferred
        acquisition parcel can therefore be consumed, transferred, or reduced during
        historical replay without forcing an acquisition-day BTC/AUD market lookup.
        The original acquisition price is fetched only when that parcel actually reaches
        a disposal inside the requested FY.
        """
        if not lot.deferred_market_price:
            return
        if not (self.period_start <= dt <= self.period_end):
            return
        p = self.market_price(
            lot.acquired,
            f"market value for external-person BTC receipt into {wallet} used by {human_date(dt)} disposal",
        )
        lot.cost = lot.qty * p
        lot.trace = (
            f"{human_date(lot.acquired)} external receipt {qb(lot.qty)} BTC at "
            f"${qmoney(p):,.2f}/BTC = ${qmoney(lot.cost):,.2f} cost base"
        )
        if lot.transition_mv is not None:
            lot.transition_gain = lot.transition_mv - lot.cost
        lot.deferred_market_price = False

    def _record_sale(self, dt: date, desc: str, wallet: str, lots: List[Lot], proceeds: Decimal, pool_qty_before: Decimal):
        """Create one report event per disposal/holding bucket.

        The disposal can consume many source lots. We keep the source-lot
        calculations as detailed notes, but aggregate those fragments into a
        single CGT event for the actual sale. Thus a sale is never combined with
        another separate sale, while the working paper still shows every
        multiplication/division used to derive its cost base and proceeds.
        """
        if not self.period_start <= dt <= self.period_end:
            return
        total = sum((l.qty for l in lots), Decimal("0"))
        if total <= BTC_EPS:
            return

        # Historical replay may include disposals before the requested FY. Do not
        # resolve deferred source ownership or acquisition prices for those disposals:
        # the parcel may be fully consumed before the target period. Resolve them only
        # for the specific lots actually consumed by a disposal inside the requested FY.
        if self.period_start <= dt <= self.period_end:
            for lot in lots:
                if lot.basis_known:
                    self._resolve_deferred_wallet_ownership(lot)
                    self._resolve_deferred_source_ownership(lot)
                    if lot.deferred_market_price:
                        self._resolve_deferred_market_price(lot, wallet, dt)

        # Historical Bitaroo deposits with no supplied source wallet are carried
        # as quantity-only reconciliation lots. Their cost base is unknown, so
        # the corresponding sale proceeds are excluded from calculated CGT totals
        # rather than assigning a fictitious zero cost base.
        unknown_lots = [l for l in lots if not l.basis_known]
        unknown_qty = sum((l.qty for l in unknown_lots), Decimal("0"))
        unknown_proceeds = (proceeds * unknown_qty / total) if unknown_qty > BTC_EPS else Decimal("0")
        if unknown_qty > BTC_EPS:
            review_key = (dt, wallet, desc)
            if review_key not in self.unreconciled_disposals:
                self.unreconciled_disposals.add(review_key)
                self.review.append(
                    f"{human_date(dt)} {wallet}: {qb(unknown_qty)} BTC of {desc} traces to a Bitaroo deposit whose source/cost basis is not present in the supplied wallet exports. "
                    f"The associated sale proceeds of ${qmoney(unknown_proceeds):,.2f} are excluded from the calculated CGT totals pending reconciliation."
                )

        sale_pct_of_pool = total / pool_qty_before if pool_qty_before > BTC_EPS else Decimal("0")

        # bucket -> accumulated values and detailed audit notes
        buckets = {}

        def bucket_state(name: str):
            if name not in buckets:
                buckets[name] = {
                    "btc": Decimal("0"), "proceeds": Decimal("0"),
                    "cost": Decimal("0"), "gl": Decimal("0"),
                    "disc": Decimal("0"), "positive": Decimal("0"), "negative": Decimal("0"), "notes": [], "sources": []
                }
            return buckets[name]

        for lot in lots:
            if not lot.basis_known:
                continue
            share_of_sale = lot.qty / total
            p = proceeds * share_of_sale
            if sale_pct_of_pool > BTC_EPS:
                source_pool_qty = lot.qty / sale_pct_of_pool
                source_pool_cost = lot.cost / sale_pct_of_pool
            else:
                source_pool_qty = lot.qty
                source_pool_cost = lot.cost

            held_days = (dt - lot.acquired).days

            def add_detail(bucket: str, cost: Decimal, gain_loss: Decimal, disc: Decimal, extra: str = ""):
                b = bucket_state(bucket)
                b["btc"] += lot.qty
                b["proceeds"] += p if bucket != "pre-reform deferred" else lot.transition_mv or Decimal("0")
                b["cost"] += cost
                b["gl"] += gain_loss
                b["positive"] += max(gain_loss, Decimal("0"))
                b["negative"] += max(-gain_loss, Decimal("0"))
                b["disc"] += disc
                if lot.source not in b["sources"] and lot.source:
                    b["sources"].append(lot.source)
                b["notes"].extend([
                    f"SOURCE LOT: acquired {human_date(lot.acquired)}; traced quantity before this sale = {qb(source_pool_qty)} BTC; traced cost base before this sale = ${qmoney(source_pool_cost):,.2f}.",
                    f"COST BASE MATH: ${qmoney(source_pool_cost):,.2f} * {sale_pct_of_pool*Decimal('100'):.12f}% = ${qmoney(cost):,.2f} allocated cost base.",
                    f"PROCEEDS MATH: ${qmoney(proceeds):,.2f} * {share_of_sale*Decimal('100'):.12f}% = ${qmoney(p):,.2f} allocated proceeds.",
                    f"HOLDING PERIOD: {held_days} days from {human_date(lot.acquired)} to {human_date(dt)}.",
                ])
                if extra:
                    b["notes"].append(extra)
                if lot.trace:
                    b["notes"].append(f"TRACE: {lot.trace} -> {human_date(dt)} disposal")

            if dt < REFORM_DATE:
                if held_twelve_months(lot.acquired, dt):
                    disc = max(p - lot.cost, Decimal("0")) * Decimal("0.50")
                    add_detail("held >= 12 months", lot.cost, p - lot.cost, disc)
                    if disc > 0:
                        buckets["held >= 12 months"]["notes"].append(
                            f"50% CGT DISCOUNT MATH: ${qmoney(p - lot.cost):,.2f} * 50% = ${qmoney(disc):,.2f} discounted gain.")
                else:
                    add_detail("held < 12 months", lot.cost, p - lot.cost, Decimal("0"))
                continue

            if lot.acquired <= REFORM_EVE and lot.transition_mv is not None:
                # Pre-reform component retained separately for the transition
                # regime. Its proceeds are the transition value; the actual
                # disposal's post-reform component uses the sale proceeds.
                dg = lot.transition_gain or Decimal("0")
                pre = bucket_state("pre-reform deferred" if held_twelve_months(lot.acquired, dt) else "pre-reform deferred short")
                pre["btc"] += lot.qty
                pre["proceeds"] += lot.transition_mv
                pre["cost"] += lot.cost
                pre["gl"] += dg
                pre["positive"] += max(dg, Decimal("0"))
                pre["negative"] += max(-dg, Decimal("0"))
                pre["disc"] += max(dg, Decimal("0")) * Decimal("0.50") if held_twelve_months(lot.acquired, dt) else Decimal("0")
                pre["sources"].append(lot.source) if lot.source and lot.source not in pre["sources"] else None
                pre["notes"].extend([
                    f"SOURCE LOT: acquired {human_date(lot.acquired)}; traced quantity before this sale = {qb(source_pool_qty)} BTC; traced cost base before this sale = ${qmoney(source_pool_cost):,.2f}.",
                    f"TRANSITION VALUE MATH: {qb(lot.qty)} BTC * transition BTC/AUD value = ${qmoney(lot.transition_mv):,.2f}.",
                    f"PRE-REFORM GAIN MATH: ${qmoney(lot.transition_mv):,.2f} - ${qmoney(lot.cost):,.2f} = ${qmoney(dg):,.2f}.",
                ])
                if dg > 0 and held_twelve_months(lot.acquired, dt):
                    pre["notes"].append(f"50% CGT DISCOUNT MATH: ${qmoney(dg):,.2f} * 50% = ${qmoney(max(dg, Decimal('0')) * Decimal('0.50')):,.2f} discounted gain.")
                if lot.trace:
                    pre["notes"].append(f"TRACE: {lot.trace} -> {human_date(dt)} disposal")
                post = bucket_state("post-reform")
                indexed = self.indexed_cost(lot.transition_mv, REFORM_DATE, dt, eligible=held_twelve_months(lot.acquired, dt))
                gl = (p - lot.transition_mv) if p < lot.transition_mv else max(Decimal("0"), p - indexed)
                post["btc"] += lot.qty
                post["proceeds"] += p
                post["cost"] += p - gl
                post["gl"] += gl
                post["positive"] += max(gl, Decimal("0"))
                post["negative"] += max(-gl, Decimal("0"))
                post["sources"].append(lot.source) if lot.source and lot.source not in post["sources"] else None
                post["notes"].extend([
                    f"SALE ALLOCATION: {qb(total)} BTC sold / {qb(pool_qty_before)} BTC in {wallet} = {sale_pct_of_pool*Decimal('100'):.12f}% of the holding.",
                    f"POST-REFORM COST BASE: transition value ${qmoney(lot.transition_mv):,.2f}; indexed value ${qmoney(indexed):,.2f}; gain/loss ${qmoney(gl):,.2f}. Indexation cannot create or enlarge a loss.",
                ])
                if lot.trace:
                    post["notes"].append(f"TRACE: {lot.trace} -> {human_date(dt)} disposal")
            else:
                if held_twelve_months(lot.acquired, dt):
                    indexed = self.indexed_cost(lot.cost, lot.acquired, dt)
                else:
                    indexed = lot.cost
                gl = p - lot.cost if p < lot.cost else max(Decimal("0"), p - indexed)
                b = bucket_state("post-reform")
                b["btc"] += lot.qty
                b["proceeds"] += p
                b["cost"] += p - gl
                b["gl"] += gl
                b["positive"] += max(gl, Decimal("0"))
                b["negative"] += max(-gl, Decimal("0"))
                b["sources"].append(lot.source) if lot.source and lot.source not in b["sources"] else None
                b["notes"].extend([
                    f"SOURCE LOT: acquired {human_date(lot.acquired)}; traced quantity before this sale = {qb(source_pool_qty)} BTC; traced cost base before this sale = ${qmoney(source_pool_cost):,.2f}.",
                    f"PROCEEDS MATH: ${qmoney(proceeds):,.2f} * {share_of_sale*Decimal('100'):.12f}% = ${qmoney(p):,.2f} allocated proceeds.",
                    f"COST BASE MATH: ${qmoney(lot.cost):,.2f} after applicable indexation = ${qmoney(indexed):,.2f}; effective cost used ${qmoney(p-gl):,.2f}. Indexation cannot create or enlarge a loss.",
                ])
                if lot.trace:
                    b["notes"].append(f"TRACE: {lot.trace} -> {human_date(dt)} disposal")

        for bucket, b in buckets.items():
            if bucket in {"held >= 12 months", "held < 12 months"}:
                b["notes"].insert(0, f"SALE ALLOCATION: {qb(total)} BTC sold / {qb(pool_qty_before)} BTC in {wallet} = {sale_pct_of_pool*Decimal('100'):.12f}% of the holding.")
                b["notes"].insert(1, f"Part of sale in this holding bucket = {qb(b['btc'])} BTC / {qb(total)} BTC = {(b['btc']/total*Decimal('100')):.12f}% of the sale.")

            source = b["sources"][0] if len(b["sources"]) == 1 else (f"Multiple source lots ({len(b['sources'])})" if b["sources"] else "")
            e = EventComponent(dt, desc + (" [pre-1-Jul-2027 deferred leg]" if bucket.startswith("pre-reform deferred") else " [post-1-Jul-2027 leg]" if bucket == "post-reform" and dt >= REFORM_DATE else ""),
                               wallet, b["btc"], b["proceeds"], b["cost"], b["gl"], bucket, b["disc"], source, b["notes"], total, pool_qty_before, proceeds, gross_gain=b["positive"], gross_loss=b["negative"])
            e.notes.insert(0, f"TRANSACTION TIME: {display_time(dt)}")
            self.events.append(e)

    def dispose(self, wallet: str, qty: Decimal, proceeds: Decimal, dt: date, desc: str):
        pool_qty_before = self.qty(wallet)
        moved = self._take(wallet, qty, desc, dt)
        start_events = len(self.events)
        self._record_sale(dt, desc, wallet, moved, proceeds, pool_qty_before)

        # Network-fee events need an explicit market-value calculation in the
        # working paper, matching the manual method used in the supplied text
        # file (BTC fee quantity x BTC/AUD price = AUD disposal proceeds).
        if "network fee" in desc.lower() and start_events < len(self.events):
            source = self.price.source_for_day.get(utc_time(dt) if isinstance(dt, TransactionDate) else dt, "automatic historical BTC/AUD lookup")
            for event in self.events[start_events:]:
                event.notes.insert(0,
                    f"FEE MARKET VALUE MATH: {qb(qty)} BTC * ${qmoney(proceeds / qty if qty > BTC_EPS else Decimal('0')):,.2f}/BTC = ${qmoney(proceeds):,.2f}."
                )
                event.notes.insert(1, f"PRICE SOURCE: {source} for {human_date(dt)} BTC/AUD valuation; timing disclosed in PRICE LOOKUPS USED.")

    def market_price(self, dt: date, context: str) -> Decimal:
        p = self.price.ask(dt, context)
        key = utc_time(dt) if isinstance(dt, (TransactionDate, datetime)) else dt
        source = self.price.source_for_day.get(key, "Unknown source")
        if key not in self._price_usage_keys:
            self._price_usage_keys.add(key)
            self.price_usage.append((dt, context, p, source))
        return p

    def acquire_from_external_person(
        self,
        wallet: str,
        qty: Decimal,
        dt: date,
        desc: str,
        source_ownership_unresolved: bool = False,
        source_wallet_ref: Optional[Tuple[str, int]] = None,
    ):
        """Record an external acquisition, optionally with deferred source ownership.

        Pre-FY acquisitions defer their BTC/AUD price. An unresolved pre-FY
        Historical Wallet ODS receipt also carries a deferred ownership decision,
        so historical ledger traversal alone does not trigger an interactive question.
        """
        if qty <= BTC_EPS:
            return

        if dt < self.period_start:
            self.add(wallet, self.new_lot(
                dt, qty, Decimal("0"), f"External acquisition: {desc}",
                f"{human_date(dt)} external receipt {qb(qty)} BTC; historical market value deferred until this parcel is used in a target-year CGT calculation",
                deferred_market_price=True,
                source_ownership_unresolved=source_ownership_unresolved,
                source_wallet_ref=source_wallet_ref,
            ))
            return

        p = self.market_price(dt, f"market value for external-person BTC receipt into {wallet}")
        self.add(wallet, self.new_lot(
            dt, qty, qty * p, f"External acquisition: {desc}",
            f"{human_date(dt)} external receipt {qb(qty)} BTC at ${qmoney(p):,.2f}/BTC = ${qmoney(qty*p):,.2f} cost base"
        ))

    def claim_wallet_fee(self, w: WalletTx) -> Decimal:
        """Return the transaction-level network fee once for this wallet send."""
        if not w.outgoing or w.fee <= BTC_EPS:
            return Decimal("0")
        if w.txid:
            if (w.sheet, w.txid) in self.claimed_wallet_fee_txids:
                return Decimal("0")
            self.claimed_wallet_fee_txids.add((w.sheet, w.txid))
            return abs(w.fee)
        # No txid means there is no safe way to prove that the fee has not already
        # been associated with another outgoing row, so do not duplicate it.
        return Decimal("0")

    def note_missing_online_fee_if_relevant(self, w: WalletTx):
        if not w.outgoing or w.fee_known or not w.txid:
            return
        if self.period_start <= w.dt <= self.period_end:
            self.review.append(
                f"{human_date(w.dt)} {w.sheet}: network fee for Bitcoin transaction {w.txid} could not be retrieved from the public transaction sources. "
                "The transfer cannot be fully reconciled until the on-chain fee is available."
            )


    def move(self, source: str, dest: str, qty: Decimal, dt: date, fee: Decimal = Decimal("0"), desc: str = "Transfer", fee_dt=None):
        moved = self._take(source, qty, f"{desc} {source} -> {dest}", dt)
        for lot in moved:
            trace = lot.trace
            if trace:
                trace = f"{trace} -> {human_date(dt)} {dest}"
            else:
                trace = f"{human_date(dt)} {source} -> {dest}"
            deferred_wallet_ownership = lot.deferred_wallet_ownership
            if self.ownership._is_historical_ods_only(source):
                wallet_status = self.ownership.wallet_statuses.get(source)
                if wallet_status != WalletOwnership.PERSONAL:
                    existing = list(deferred_wallet_ownership)
                    item = (source, dt)
                    if not existing or existing[-1] != item:
                        existing.append(item)
                    deferred_wallet_ownership = tuple(existing)
            self.add(dest, replace(lot, trace=trace, deferred_wallet_ownership=deferred_wallet_ownership))
        if fee > BTC_EPS:
            fee_when = dt if fee_dt is None else fee_dt
            if self.period_start <= fee_when <= self.period_end:
                p = self.market_price(fee_when, f"network fee: {desc} {source} -> {dest}")
                self.dispose(source, fee, fee * p, fee_when, "Network fee")
            else:
                self._take(source, fee, "Historical network fee", fee_when)

    # ---------- reform ----------
    def ask_transition_price(self):
        boundary = local_to_utc(datetime(2027, 7, 1)) - timedelta(microseconds=1)
        return self.market_price(TransactionDate(boundary), "market value immediately before CGT reform")

    def apply_transition(self, price: Decimal):
        if self.transition_applied:
            return
        self.transition_price = price
        for lots in self.holdings.values():
            for lot in lots:
                if lot.basis_known and lot.acquired <= REFORM_EVE and lot.qty > BTC_EPS:
                    mv = lot.qty * price
                    lot.transition_mv = mv
                    # A historical external-acquisition lot may still have a
                    # deferred acquisition-day market value. The transition value
                    # itself is known now, but its pre-reform gain is deferred until
                    # the acquisition cost is actually needed by a later disposal.
                    lot.transition_gain = None if lot.deferred_market_price else mv - lot.cost
        self.transition_applied = True

    def indexed_cost(self, cost: Decimal, acquisition: date, disposal: date, eligible=None) -> Decimal:
        if eligible is False or (eligible is None and not held_twelve_months(acquisition, disposal)):
            return cost
        c1 = self.get_cpi(quarter_key(acquisition))
        c2 = self.get_cpi(quarter_key(disposal))
        factor = (c2 / c1).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return cost * factor if factor > 1 else cost

    def get_cpi(self, q: str) -> Decimal:
        if CPI_VALUES:
            value = CPI_VALUES.get(q)
            if value is None or value <= 0:
                raise ValueError(f"Missing verified ABS CPI for {q} in --cpi. Supply all needed quarters on one consistent reference base.")
            return value
        if not hasattr(self, '_cpi_cache'):
            self._cpi_cache = {}
        if q not in self._cpi_cache:
            period = q[:4] + '-Q' + q[-1]
            url = ('https://data.api.abs.gov.au/rest/data/ABS,CPI,2.0.0/1.10001.10.50.Q?'
                   + urllib.parse.urlencode({'startPeriod': period, 'endPeriod': period}))
            try:
                raw = cached_http(url, {'Accept': 'application/vnd.sdmx.data+csv;version=1.0.0'}).decode('utf-8-sig')
                rows = list(csv.DictReader(io.StringIO(raw)))
                rows = [r for r in rows if r.get('TIME_PERIOD') == period and r.get('MEASURE') == '1'
                        and r.get('INDEX') == '10001' and r.get('TSEST') == '10'
                        and r.get('REGION') == '50' and r.get('FREQ') == 'Q']
                if len(rows) != 1:
                    raise ValueError('Requested quarterly All Groups index not uniquely available')
                value = D(rows[0]['OBS_VALUE'])
                base = rows[0].get('BASE_PERIOD')
                if hasattr(self, '_cpi_base') and self._cpi_base != base:
                    raise ValueError('ABS CPI reference base changed between queries; supply a consistent --cpi file')
                self._cpi_base = base
                if not value.is_finite() or value <= 0:
                    raise ValueError('Invalid CPI value')
                self._cpi_cache[q] = value
            except (ValueError, KeyError, urllib.error.URLError, OSError) as exc:
                raise RuntimeError(f"ABS CPI {q} is unavailable/unpublished: {exc}. Supply --cpi with verified consistent quarterly indices when published.") from exc
        return self._cpi_cache[q]

    # ---------- Other Exchange / foreign fiat ----------
    def other_exchange_aud_value(self, row: OtherExchangeRow) -> Tuple[Decimal, Decimal, str]:
        if row.ref in self._exchange_values:
            return self._exchange_values[row.ref]
        original = abs(row.fiat)
        other_fee = sum((abs(amount) for _, amount, unit in row.fees if unit == row.currency), Decimal('0'))
        if exchange_asset_is_fiat(row):
            rate, source = self.fx.close_to_aud(row.currency, row.dt)
            # B is the principal fiat spend on buys; E is net fiat received on
            # sales. Fees in the bought unit are already withheld from E.
            aud_value = (original + other_fee if row.buy else original) * rate
            if row.currency != "AUD":
                self.fx_usage.append((row.dt, row.currency, rate, aud_value, source))
        else:
            price = self.market_price(row.dt, f"{row.exchange} {row.currency}/BTC exchange")
            gross_btc = abs(row.btc) + (row.btc_fee_amount if row.buy else Decimal('0'))
            aud_value = gross_btc * price
            source = f"BTC-leg market value; {row.currency} is a crypto asset"
            if row.buy and other_fee:
                if not original:
                    raise ValueError('A crypto acquisition fee requires a nonzero sold quantity')
                aud_value += other_fee * aud_value / original
            if row.sell and other_fee:
                # E is net crypto received; infer the received asset's unit
                # value from gross consideration, without a fiat lookup.
                aud_value *= original / (original + other_fee)
        if row.sell and row.btc_fee_amount:
            aud_value -= row.btc_fee_amount * self.market_price(row.dt, f"{row.exchange} BTC fees")
            if aud_value < 0:
                raise ValueError(f"{row.exchange} row {row.row_num}: fees exceed gross proceeds")
        self._exchange_values[row.ref] = (original, aud_value, source)
        return self._exchange_values[row.ref]

    def record_other_exchange_acquisition(self, row, wallet, aud_value, trace):
        # Incoming BTC in E is net of any BTC-denominated fee. Reconstruct
        # the gross acquisition, then dispose of each actual BTC fee once.
        # The fee market value is incidental acquisition cost; its BTC cost
        # basis is consumed using the ledger's existing pro-rata policy.
        lot = self.new_lot(row.dt, row.btc + row.btc_fee_amount, aud_value,
                           f"{row.exchange} buy", trace, basis_known=True)
        self.add(wallet, lot)
        for label, signed, unit in row.fees:
            if unit != 'BTC' or not signed:
                continue
            amount = abs(signed)
            value = amount * self.market_price(row.dt, f"{row.exchange} {label}")
            self.dispose(wallet, amount, value, row.dt, f"{row.exchange} {label} (withheld from gross BTC acquisition)")
            remaining = next((part for part in self.holdings[wallet] if part.id == lot.id), None)
            if remaining is None:
                raise ValueError(f"{row.exchange} row {row.row_num}: no retained acquisition for fee allocation")
            remaining.cost += value

    def find_other_exchange_buy(self, qty: Decimal, dt: date) -> Optional[OtherExchangeRow]:
        """Match an Other Exchange buy to a Bitaroo deposit.

        Matching is intentionally constrained to the same mechanism used for
        wallet -> Bitaroo transfers: the BTC quantities must match exactly
        within the normal 2e-8 rounding tolerance and the dates must be within
        the configured elapsed-time tolerance. There is deliberately NO quantity-only fallback.
        A repeated BTC amount elsewhere in the Other Exchange ledger must never be
        allowed to claim an unrelated Bitaroo deposit.
        """
        tolerance = Decimal("0.00000002")
        candidates = []
        for row in self.other_exchange_rows:
            if row.dt > self.period_end:
                continue
            if not row.buy or row.ref in self.matched_other_exchange_deposits or row.ref in self.matched_other_exchange_wallet_buys:
                continue
            if abs(row.btc - qty) > tolerance or not date_close(row.dt, dt):
                continue
            candidates.append((date_distance(row.dt, dt), row.exchange.lower(), row.row_num, row))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1], x[2]))
        return candidates[0][3]

    def _unique_bitaroo_deposit_for_other_exchange(self, row: OtherExchangeRow) -> Optional[BitarooRow]:
        """Return the unique Bitaroo Deposit corroborating an Other Exchange buy.

        The match is limited to exact BTC quantity within the normal tolerance and
        the configured elapsed-time tolerance. An ambiguous match is not guessed.
        """
        tolerance = Decimal("0.00000002")
        candidates = []
        for r in self.reports.btc_rows:
            if r.tx_type != "Deposit" or r.amount <= BTC_EPS:
                continue
            if abs(r.amount - row.btc) > tolerance:
                continue
            if not date_close(tax_date(r.dt), row.dt):
                continue
            candidates.append(r)
        candidates.sort(key=lambda r: (date_distance(r.dt, row.dt), r.dt))
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _wallet_has_active_rows(self, wallet: str) -> bool:
        """Return whether this wallet participates in the current dependency graph.

        The CGT engine may know about many historical wallets that are completely
        irrelevant to the selected financial year.  Ownership is a semantic boundary
        and should only be requested once the trace actually needs that wallet.

        ``active_wallet_rows`` is normally the HistoryPlanner's required wallet-row
        set.  ``None`` is retained as a compatibility mode for callers that use the
        Engine without a planner; in that case the historical V61 behaviour applies.
        """
        if wallet == "Bitaroo":
            return True
        if self.active_wallet_rows is None:
            return True
        return any(w.ref in self.active_wallet_rows for w in self.wallets.get(wallet, []))

    def _wallet_has_active_target_year_row(self, wallet: str) -> bool:
        """Return whether this wallet has an active required row inside the target FY."""
        rows = self.wallets.get(wallet, [])
        for w in rows:
            if self.active_wallet_rows is not None and w.ref not in self.active_wallet_rows:
                continue
            if self.period_start <= w.dt <= self.period_end:
                return True
        return False

    def _trace_source_for_bitaroo_row(self, row):
        if self.primary_bitaroo_rows is not None and id(row) not in self.primary_bitaroo_rows:
            return "Wallet Tracer"
        return "Exchange Tracer"

    def bitaroo_transaction_reference(self, row):
        ordinal = next((i for i, r in enumerate(
            (r for r in self.reports.btc_rows if r.source_file == row.source_file), 1
        ) if r is row), None)
        if ordinal is None:
            raise ValueError("Bitaroo transaction row is missing from its source report")
        return f"PDF:{Path(row.source_file).name}:page{row.page}:row{ordinal}"

    def _trace_source_for_wallet_row(self, w: WalletTx) -> str:
        primary = getattr(self, "primary_wallet_rows", None)
        if primary is not None and w.ref not in primary:
            return "Wallet Tracer"
        return "Exchange Tracer"

    def seed_other_exchange_acquisitions(self, through_date: Optional[date] = None, through_instant=None):
        """Seed Other Exchange acquisitions only through the supplied ledger date.

        Historical acquisitions can be needed during the pre-opening replay. Target-FY
        acquisitions are intentionally deferred until their own date so they cannot
        contaminate the authoritative target-FY Bitaroo opening checkpoint.
        """
        cutoff = self.period_end if through_date is None else min(through_date, self.period_end)
        for row in self.other_exchange_rows:
            if not row.buy or row.dt > cutoff or row.ref in self.seeded_other_exchange_rows:
                continue

            if through_instant is not None and isinstance(row.dt, TransactionDate) and utc_time(row.dt) > through_instant:
                continue
            supplied_receipts = [w for group in self.wallets.values() for w in group
                if w.incoming and abs(w.principal-row.btc) <= Decimal("0.00000002")
                and date_close(w.dt, row.dt)]
            if (self.active_wallet_rows is not None and supplied_receipts
                    and not any(w.ref in self.active_wallet_rows for w in supplied_receipts)):
                continue
            wallet = ""
            matched_wallet_row: Optional[WalletTx] = None

            # First identify *evidenced* receiving-wallet candidates using only
            # wallet rows which are already in the current dependency graph.
            # Only then ask ownership for those candidate wallets.  This prevents
            # unrelated historical wallets from generating ownership questions.
            candidate_by_wallet: Dict[str, List[WalletTx]] = defaultdict(list)
            for wallet_name, wallet_rows in list(self.wallets.items()):
                if wallet_name == "Bitaroo":
                    continue
                for w in wallet_rows:
                    if self.active_wallet_rows is not None and w.ref not in self.active_wallet_rows:
                        continue
                    if w.dt > self.period_end:
                        continue
                    if w.ref in self.matched_other_exchange_wallet_receipts:
                        continue
                    if not w.incoming:
                        continue
                    if abs(w.principal - row.btc) > Decimal("0.00000002"):
                        continue
                    if not date_close(w.dt, row.dt):
                        continue
                    candidate_by_wallet[wallet_name].append(w)

            # Preserve V61's ambiguity rule, but move the ownership decision to
            # the point where a wallet has actually been shown to be a candidate.
            candidates: List[WalletTx] = []
            for wallet_name, wallet_rows in candidate_by_wallet.items():
                candidate_dt = min((w.dt for w in wallet_rows), default=self.period_start)
                require = self.ownership.ownership_required_for_row(wallet_name, candidate_dt, self.period_start, self.period_end)
                if self.ownership.planning_wallet_status(wallet_name, require_ownership=require) == WalletOwnership.PERSONAL:
                    candidates.extend(wallet_rows)

            candidates.sort(key=lambda w: (date_distance(w.dt, row.dt), w.sheet.lower(), w.row_num))
            if len(candidates) == 1:
                matched_wallet_row = candidates[0]
                competing = [other for other in self.other_exchange_rows
                    if other.buy and other.ref not in self.seeded_other_exchange_rows
                    and abs(other.btc-matched_wallet_row.principal) <= Decimal("0.00000002")
                    and date_close(other.dt, matched_wallet_row.dt)]
                if len(competing) > 1:
                    raise ValueError(f"{row.exchange} row {row.row_num}: multiple exchange buys match the same wallet receipt; provide precise transaction times to disambiguate.")
                wallet = matched_wallet_row.sheet
                self.matched_other_exchange_wallet_receipts.add(matched_wallet_row.ref)
                self.matched_other_exchange_wallet_buys.add(row.ref)
                self.other_exchange_match_notes.append(
                    f"{human_date(row.dt)} {row.exchange} buy {qb(row.btc)} BTC matched to "
                    f"{human_date(matched_wallet_row.dt)} {matched_wallet_row.sheet} incoming {qb(matched_wallet_row.principal)} BTC "
                    f"within the configured elapsed-time tolerance; Other Exchange spreadsheet supplied the acquisition cost."
                )
            elif candidates:
                raise ValueError(f"{row.exchange} row {row.row_num}: multiple personal wallet receipts match this quantity/date; provide precise transaction times to disambiguate.")
            else:
                # Unwithdrawn acquisitions remain at the exchange named by the
                # input tab. Only an evidenced deposit assigns another venue.
                wallet = "Bitaroo" if self._unique_bitaroo_deposit_for_other_exchange(row) is not None else row.exchange

            # When an Other Exchange buy has an unambiguous corroborating Bitaroo
            # Deposit, let the Bitaroo deposit event create the acquisition lot at
            # the point where that historical exchange row actually enters the
            # reconstructed Bitaroo ledger. This prevents a pre-FY acquisition from
            # being globally seeded at Bitaroo and surviving after a historical
            # withdrawal that the target dependency graph never needed to replay.
            if matched_wallet_row is None and wallet == "Bitaroo" and self._unique_bitaroo_deposit_for_other_exchange(row) is not None:
                continue

            self.other_exchange_wallets[row.ref] = wallet
            original, aud_value, _source = self.other_exchange_aud_value(row)
            trace = (
                f"{human_date(row.dt)} {row.exchange} Buy {qb(row.btc)} BTC for "
                f"{row.currency} {qmoney(original):,.2f} = ${qmoney(aud_value):,.2f} AUD"
            )
            self.record_other_exchange_acquisition(row, wallet, aud_value, trace)
            if matched_wallet_row is not None:
                # The source wallet receipt is already represented by the seeded
                # exchange acquisition lot. Do not process the same incoming row as
                # a gift/external receipt or attempt to trace it to an absent wallet.
                self.processed_wallet.add(matched_wallet_row.ref)
            self.seeded_other_exchange_rows.add(row.ref)

    # ---------- report history ----------


    # ---------- matching helpers ----------
    def _wallet_rows(self):
        all_rows = []
        for rows in self.wallets.values():
            for w in rows:
                if self.active_wallet_rows is None or w.ref in self.active_wallet_rows:
                    all_rows.append(w)
        return all_rows

    def find_wallet_incoming_for_withdrawal(self, qty: Decimal, dt) -> Optional[WalletTx]:
        cands = []
        target = self.period_start <= tax_date(dt) <= self.period_end
        rows = [w for group in self.wallets.values() for w in group]
        for w in rows:
            if w.dt > self.period_end or w.ref in self.processed_wallet or not w.incoming:
                continue
            if abs(w.principal - qty) <= Decimal("0.00000002") and date_close(w.dt, dt):
                cands.append((date_distance(w.dt, dt), abs(w.principal - qty), w))
        if not cands:
            return None
        cands.sort(key=lambda x: (x[0], x[1], x[2].sheet, x[2].row_num))
        best = [x for x in cands if x[:2] == cands[0][:2]]
        if len(best) != 1:
            return None
        recipient = best[0][2]
        if target:
            name = self.ownership.ensure_wallet_identity(self.wallets, recipient.sheet)
            self.ownership.planning_wallet_status(name, require_ownership=True)
        return recipient

    def find_wallet_exchange_outgoing(self, qty: Decimal, dt: date) -> Optional[WalletTx]:
        cands = []
        for w in self._wallet_rows():
            if w.dt > self.period_end:
                continue
            if w.ref in self.processed_wallet or not w.outgoing:
                continue
            match = match_bitaroo_deposit_row(self.reports, w)
            if match is None:
                continue
            matched_qty, _, dep = match
            if abs(matched_qty - qty) <= Decimal("0.00000002") and date_close(w.dt, dt):
                cands.append((date_distance(dep.dt, dt), date_distance(w.dt, dt), w))
        cands.sort(key=lambda x: (x[0], x[1], x[2].sheet, x[2].row_num))
        return cands[0][2] if cands else None

    def find_wallet_dest_for_row(self, w: WalletTx) -> Optional[str]:
        match = find_loaded_wallet_counterparty(self.wallets, w.sheet, w)
        if match is None:
            return None
        return self.ownership.ensure_wallet_identity(self.wallets, match[0])

    # ---------- event timeline ----------
    def build_events(self) -> List[LedgerEvent]:
        events: List[LedgerEvent] = []

        # Resolve supplied sales to their funding transfer once, before replay.
        # A transfer into an exchange is not a second disposal of the same BTC.
        self.other_sale_sources={};self.other_sale_transfers={}
        for row in self.other_exchange_rows:
            if not row.sell or row.dt>self.period_end:continue
            candidates=[]
            for name,rows in self.wallets.items():
                if self.ownership.wallet_statuses.get(name)!=WalletOwnership.PERSONAL:continue
                for w in rows:
                    if w.outgoing and w.dt<=self.period_end and match_other_exchange_sale([row],w.principal,w.dt) and self.find_wallet_dest_for_row(w) is None and match_bitaroo_deposit_row(self.reports,w) is None:
                        candidates.append(('wallet',w))
            for r in self.reports.btc_rows:
                if r.tx_type=='Withdrawal' and tax_date(r.dt)<=self.period_end and self.find_wallet_incoming_for_withdrawal(abs(r.amount),r.dt) is None and match_other_exchange_sale([row],abs(r.amount),tax_date(r.dt)):
                    candidates.append(('bitaroo',r))
            if len(candidates)>1:raise ValueError(f'{row.exchange} row {row.row_num}: multiple transfers match this exchange sale.')
            if candidates:
                kind,source=candidates[0];key=(kind,source.ref if kind=='wallet' else id(source))
                if key in self.other_sale_transfers:raise ValueError('One transfer cannot fund two supplied exchange sales.')
                self.other_sale_transfers[key]=row;self.other_sale_sources[row.ref]=(kind,source)

        # Bitaroo rows.
        for i, r in enumerate(self.reports.btc_rows):
            if self.active_bitaroo_rows is not None and id(r) not in self.active_bitaroo_rows:
                continue
            if tax_date(r.dt) > self.period_end:
                continue
            if r.tx_type in {"Express Buy", "Buy Bitcoin"} and r.amount > 0:
                events.append(LedgerEvent(r.dt, "bitaroo_buy", (i, r)))
            elif r.tx_type in {"Sell Bitcoin", "Express Sell"} and r.amount < 0:
                events.append(LedgerEvent(r.dt, "bitaroo_sell", (i, r)))
            elif r.tx_type == "Withdrawal" and r.amount < 0:
                recipient = self.find_wallet_incoming_for_withdrawal(abs(r.amount), r.dt)
                # Exchange-side deduction is authoritative at the Bitaroo UTC
                # timestamp. The recipient wallet is credited separately at its
                # workbook date and row order.
                events.append(LedgerEvent(r.dt, "bitaroo_withdrawal", (i, r, recipient)))
                if recipient is not None and self.ownership.wallet_statuses.get(recipient.sheet) != WalletOwnership.EXTERNAL:
                    wallet_when = max(r.dt, utc_time(recipient.dt).replace(tzinfo=None))
                    events.append(LedgerEvent(wallet_when, "wallet_from_exchange", (recipient, i, r)))
            elif r.tx_type == "Withdrawal Fee" and r.amount < 0:
                events.append(LedgerEvent(r.dt, "bitaroo_fee", (i, r)))
            elif r.tx_type == "Deposit" and r.amount > 0:
                events.append(LedgerEvent(r.dt, "bitaroo_deposit_validation", (i, r)))

        # Each exchange row has a ledger event, including a last/only acquisition
        # with no later wallet activity. Preserve supplied instants; date-only
        # rows use the existing noon ordering convention.
        for row in self.other_exchange_rows:
            if row.dt > self.period_end:
                continue
            if row.buy or row.sell:
                events.append(LedgerEvent(
                    max(utc_time(row.dt),utc_time(self.other_sale_sources[row.ref][1].dt)).replace(tzinfo=None) if row.ref in self.other_sale_sources else utc_time(row.dt).replace(tzinfo=None),
                    'other_exchange_buy' if row.buy else 'other_exchange_sell',
                    row,
                ))

        # Wallet outgoing transfers are the ledger-changing side of wallet movements.
        # A wallet -> Bitaroo transfer is established only by direct BTC/date matching.
        # Loaded wallet destinations are established only from transaction evidence.
        for wallet_name, rows in list(self.wallets.items()):
            if self.active_wallet_rows is not None and not any(
                w.ref in self.active_wallet_rows for w in rows
            ):
                continue
            for w in rows:
                if self.active_wallet_rows is not None and w.ref not in self.active_wallet_rows:
                    continue
                if w.dt > self.period_end or not w.outgoing or w.ref in self.processed_wallet:
                    continue

                source_status = self.ownership.wallet_statuses.get(wallet_name)
                if source_status == WalletOwnership.EXTERNAL:
                    match = match_bitaroo_deposit_row(self.reports, w)
                    if match is not None:
                        events.append(LedgerEvent(match[2].dt, "wallet_to_exchange", w))
                    continue
                dest = self.find_wallet_dest_for_row(w)
                direct_bitaroo_match = match_bitaroo_deposit_row(self.reports, w)

                # A specific Bitaroo Deposit match is authoritative for this wallet
                # row. Once the matcher has established that this exact output is the
                # exchange deposit, the ordinary Bitaroo path applies and no separate
                # destination-address ownership question is appropriate.
                if dest is None and direct_bitaroo_match is not None:
                    events.append(LedgerEvent(
                        max(direct_bitaroo_match[2].dt,utc_time(w.dt).replace(tzinfo=None)) if w.accounting_flow.endswith(':bridge') else direct_bitaroo_match[2].dt,
                        "wallet_to_exchange",
                        w,
                    ))
                    continue

                # All other personal-wallet sends must enter the ledger, even
                # when a date-only ODS row has no destination address.
                events.append(LedgerEvent(utc_time(w.dt).replace(tzinfo=None), "wallet_out", w))

        # Incoming wallet rows are acquisitions only when their source is external.
        # Known wallet senders, Other Exchange buys, and Bitaroo withdrawals are not
        # separate external acquisition events.
        for wallet_name, rows in list(self.wallets.items()):
            if self.active_wallet_rows is not None and not any(
                w.ref in self.active_wallet_rows for w in rows
            ):
                continue
            for w in rows:
                if self.active_wallet_rows is not None and w.ref not in self.active_wallet_rows:
                    continue
                if w.dt > self.period_end or not w.incoming:
                    continue
                if self.ownership.wallet_statuses.get(wallet_name) == WalletOwnership.EXTERNAL:
                    continue

                source_match = find_loaded_wallet_counterparty(self.wallets, w.sheet, w)
                if source_match is not None:
                    src, _ = source_match
                    # This is topology reconstruction, not an ownership decision.
                    # Historical ODS ownership is carried on the actual lot lineage
                    # and resolved only if that parcel reaches a target-FY disposal.
                    # Descriptor wallets still prompt when they become relevant.
                    require = self.ownership.ownership_required_for_row(src, self.wallets[src][source_match[1]].dt, self.period_start, self.period_end)
                    if self.ownership.planning_wallet_status(src, require_ownership=require) == WalletOwnership.PERSONAL:
                        continue
                    events.append(LedgerEvent(utc_time(w.dt).replace(tzinfo=None), "wallet_external_in", w))
                    continue

                if match_other_exchange_buy_to_wallet_receipt(self.other_exchange_rows, w, max_date=self.period_end) is not None:
                    continue
                if match_bitaroo_withdrawal_row(self.reports, w) is not None:
                    continue
                if self.period_start <= w.dt <= self.period_end:
                    # For target-year receipts, source ownership determines whether
                    # this is an internal movement or a new acquisition from somebody
                    # else. Do not silently assume an absent source is external.
                    self.ownership.decide_incoming_source(w)
                events.append(LedgerEvent(utc_time(w.dt).replace(tzinfo=None), "wallet_external_in", w))

        # Transition is an internal marker at midnight before the first post-
        # reform event. Actual application happens after all pre-reform events.
        if self.period_end >= REFORM_DATE:
            events.append(LedgerEvent(local_to_utc(datetime.combine(REFORM_DATE, datetime.min.time())).replace(tzinfo=None), "transition_marker", None))

        # Sort so a same-date early buy/deposit/withdrawal is processed before
        # generic wallet noon rows, and sales/fees keep Bitaroo's timestamp.
        priority = {
            "other_exchange_buy": 1,
            "wallet_external_in": 2,
            "bitaroo_buy": 10,
            "other_exchange_sell": 15,
            "bitaroo_withdrawal": 20,
            "bitaroo_deposit_validation": 21,
            "wallet_to_exchange": 30,
            "wallet_out": 40,
            "wallet_from_exchange": 41,
            "bitaroo_sell": 50,
            "bitaroo_fee": 60,
            "transition_marker": 0,
        }

        def event_sort_key(e: LedgerEvent):
            sheet = ""
            row_num = 0
            if isinstance(e.payload, WalletTx):
                sheet, row_num = globals().get("DESCRIPTOR_SOURCE_NAMES", {}).get(e.payload.descriptor_id, e.payload.sheet), e.payload.row_num
            elif isinstance(e.payload, tuple):
                for part in e.payload:
                    if isinstance(part, WalletTx):
                        sheet, row_num = globals().get("DESCRIPTOR_SOURCE_NAMES", {}).get(part.descriptor_id, part.sheet), part.row_num
                        break
            sale_after_funding = e.kind == 'other_exchange_sell' and e.payload.ref in self.other_sale_sources
            return (e.when, 45 if sale_after_funding else priority.get(e.kind, 90), sheet, row_num)

        events.sort(key=event_sort_key)
        return events

    def _trade_for(self, r: BitarooRow, side: str) -> Optional[Trade]:
        q = self._trade_lookup.get((r.dt, side), [])
        if not q:
            return None

        # Trade pairing is established before same-timestamp chronological
        # normalization.  Reuse the occurrence stored on the BTC row so the AUD
        # proceeds remain attached to the exact statement trade even after the
        # ledger order has been corrected.
        if r.trade_occurrence is not None:
            return q[r.trade_occurrence] if r.trade_occurrence < len(q) else q[0]

        # Fallback for any row created by older/internal code that has no stored
        # statement occurrence.
        target_occ = 0
        for x in self.reports.btc_rows:
            if x.dt != r.dt:
                continue
            xs = "buy" if x.tx_type in {"Express Buy", "Buy Bitcoin"} and x.amount > 0 else \
                 "sell" if x.tx_type in {"Sell Bitcoin", "Express Sell"} and x.amount < 0 else None
            if xs != side:
                continue
            if x is r:
                break
            target_occ += 1
        return q[target_occ] if target_occ < len(q) else q[0]

    def process_event(self, ev: LedgerEvent):
        kind = ev.kind
        if kind == "wallet_external_in":
            w: WalletTx = ev.payload
            trace_source = self._trace_source_for_wallet_row(w)
            if w.ref in self.processed_wallet:
                return
            source_match = find_loaded_wallet_counterparty(self.wallets, w.sheet, w)
            if source_match is not None:
                src, _ = source_match
                src = self.ownership.ensure_wallet_identity(self.wallets, src)
                src_idx = source_match[1]
                src_dt = self.wallets[src][src_idx].dt
                require = self.ownership.ownership_required_for_row(src, src_dt, self.period_start, self.period_end)
                src_status = self.ownership.planning_wallet_status(src, require_ownership=require)
                if src_status == WalletOwnership.PERSONAL:
                    # Internal transfer will be processed from the sender side.
                    return
                source_desc = f"external wallet {src}"
            else:
                source_desc = "external source"
            unresolved_historical_ods = (
                w.is_historical_ods
                and w.dt < self.period_start
                and source_match is None
            )
            self.acquire_from_external_person(
                w.sheet,
                w.principal,
                w.dt,
                f"gift received from {source_desc}",
                source_ownership_unresolved=unresolved_historical_ods,
                source_wallet_ref=w.ref if unresolved_historical_ods else None,
            )
            self.processed_wallet.add(w.ref)
            return

        if kind == "other_exchange_buy":
            row = ev.payload
            self.seed_other_exchange_acquisitions(through_date=row.dt, through_instant=utc_time(row.dt))
            return

        if kind == "other_exchange_sell":
            row: OtherExchangeRow = ev.payload
            original, proceeds, source = self.other_exchange_aud_value(row)
            start_events = len(self.events)
            self.dispose(
                self.other_exchange_wallets.get(row.ref, row.exchange),
                abs(row.btc),
                proceeds,
                row.dt,
                f"{row.exchange} sale {qb(abs(row.btc))} BTC for {row.currency} {qmoney(original):,.2f}",
            )
            principal_events = self.events[start_events:]
            for label, signed, unit in row.fees:
                if unit != 'BTC' or not signed:
                    continue
                amount = abs(signed)
                fee_value = amount * self.market_price(row.dt, f"{row.exchange} {label}")
                self.dispose(self.other_exchange_wallets.get(row.ref, row.exchange), amount, fee_value, row.dt,
                             f"{row.exchange} {label} (additional to principal)")
            for event in principal_events:
                event.notes.insert(0,
                    f"OTHER EXCHANGE SALE: {row.exchange}; net received consideration {original:f} {row.currency}; "
                    f"net proceeds ${qmoney(proceeds):,.2f} AUD after any additional BTC fees."
                )
                event.notes.insert(1, f"VALUATION SOURCE: {source} for {human_date(row.dt)}.")
            return

        if kind == "transition_marker":
            if not self.transition_applied:
                eligible = any(lot.acquired < REFORM_DATE and lot.basis_known for lots in self.holdings.values() for lot in lots)
                if eligible:
                    self.apply_transition(self.transition_price if self.transition_price is not None else self.ask_transition_price())
                else:
                    self.transition_applied = True
            return

        if kind == "bitaroo_buy":
            _, r = ev.payload
            t = self._trade_for(r, "buy")
            if t is None:
                self.review.append(f"{r.dt} Bitaroo buy {qb(r.amount)} BTC has no matching AUD trade.")
            else:
                self.add("Bitaroo", self.new_lot(tax_date(r.dt), r.amount, t.aud, "Bitaroo buy", f"{human_date(tax_date(r.dt))} Bitaroo Buy {qb(r.amount)} BTC for ${qmoney(t.aud):,.2f}"))
            return

        if kind == "bitaroo_sell":
            _, r = ev.payload
            t = self._trade_for(r, "sell")
            if t is None:
                self.review.append(f"{r.dt} Bitaroo sale {qb(abs(r.amount))} BTC has no matching AUD proceeds.")
            else:
                proceeds = t.aud
                # Optional allocation of same-time Bitaroo sell fees.
                if INCLUDE_FIAT_DISPOSAL_FEES:
                    fees = self.reports.fee_rows.get(r.dt, [])
                    occ = t.occurrence
                    if occ < len(fees):
                        proceeds -= fees[occ]
                self.dispose("Bitaroo", abs(r.amount), proceeds, tax_date(r.dt),
                             f"Bitaroo sale {qb(abs(r.amount))} BTC for ${qmoney(t.aud):,.2f}")
                if tax_date(r.dt) < self.period_start and abs(r.balance) <= BITAROO_DUST_BALANCE_EPS:
                    self.holdings["Bitaroo"] = []
            return

        if kind == "bitaroo_withdrawal":
            _, r, recipient = ev.payload
            qty = abs(r.amount)
            sale=getattr(self,'other_sale_transfers',{}).get(('bitaroo',id(r)))
            if sale is not None:
                self.move('Bitaroo',sale.exchange,qty,tax_date(r.dt),desc='Transfer funding supplied exchange sale')
                return
            if recipient is None:
                dt = tax_date(r.dt)
                fy = r.dt.year if r.dt.month >= 7 else r.dt.year - 1
                self.ownership.decide_unresolved_destination(
                    f"Bitaroo {fmt_fy(fy)} FY", dt, qty,
                    self.bitaroo_transaction_reference(r),
                    trace_source=self._trace_source_for_bitaroo_row(r),
                )
                if self.period_start <= dt <= self.period_end:
                    price = self.market_price(dt, "Bitaroo transfer to external destination")
                    self.dispose("Bitaroo", qty, qty * price, dt,
                                 "Transfer to external destination (taxable disposal)")
                else:
                    self._take("Bitaroo", qty, "Historical transfer to external destination", dt)
                if dt < self.period_start and abs(r.balance) <= Decimal("0.00010001"):
                    self.holdings["Bitaroo"] = []
                return

            status = self.ownership.planning_wallet_status(recipient.sheet, require_ownership=True)
            if status == WalletOwnership.EXTERNAL:
                dt = tax_date(r.dt)
                if self.period_start <= dt <= self.period_end:
                    price = self.market_price(dt, f"Bitaroo gift to external wallet {recipient.sheet}")
                    self.dispose("Bitaroo", qty, qty * price, dt, f"Transfer to {recipient.sheet} (external wallet — taxable disposal)")
                else:
                    self._take("Bitaroo", qty, "Historical gift from Bitaroo", dt)
                self.processed_wallet.add(recipient.ref)
                return

            # Remove the parcel from Bitaroo now, but defer crediting the recipient
            # wallet until the wallet source data row occurs. This preserves wallet
            # row ordering and prevents old lots from surviving across a same-day
            # zeroing transfer.
            available = self.qty("Bitaroo")
            move_qty = min(qty, available)
            moved = self._take("Bitaroo", move_qty, "Bitaroo withdrawal", tax_date(r.dt)) if move_qty > BTC_EPS else []
            withdrawal_idx = self.reports.btc_rows.index(r)
            key = (recipient.sheet, recipient.row_num, withdrawal_idx)
            self.pending_withdrawals[key] = moved

            if move_qty + BTC_EPS < qty:
                # This is an historical pre-floor quantity gap. Preserve only the
                # BTC whose cost basis is actually reconstructed from the supplied
                # Bitaroo statements; do not turn the unavoidable pre-floor gap into
                # a target-year review item or invent a basis for it.
                pass

            if tax_date(r.dt) < self.period_start and abs(r.balance) <= Decimal("0.00010001"):
                self.holdings["Bitaroo"] = []
            return

        if kind == "wallet_from_exchange":
            recipient, withdrawal_idx, _ = ev.payload
            if recipient.ref in self.processed_wallet:
                return
            key = (recipient.sheet, recipient.row_num, withdrawal_idx)
            moved = self.pending_withdrawals.get(key, [])
            if not moved:
                self.review.append(
                    f"{human_date(recipient.dt)} {recipient.sheet}: incoming {qb(recipient.principal)} BTC "
                    f"from Exchange has no traced Bitaroo parcel."
                )
                self.processed_wallet.add(recipient.ref)
                return

            for lot in moved:
                trace = lot.trace
                if trace:
                    trace = f"{trace} -> {human_date(recipient.dt)} {recipient.sheet}"
                else:
                    trace = f"{human_date(recipient.dt)} Bitaroo -> {recipient.sheet}"
                self.add(recipient.sheet, replace(lot, trace=trace))

            self.processed_wallet.add(recipient.ref)
            return

        if kind == "bitaroo_fee":
            _, r = ev.payload
            qty = abs(r.amount)
            if self.period_start <= tax_date(r.dt) <= self.period_end:
                # Only a Bitaroo network fee occurring inside the requested
                # Financial Year is itself a CGT disposal for this working paper.
                p = self.market_price(tax_date(r.dt), f"Bitaroo network fee {qb(qty)} BTC")
                self.dispose("Bitaroo", qty, qty * p, tax_date(r.dt), f"Bitaroo network fee {qb(qty)} BTC")
            else:
                # Historical exchange withdrawal fees are deliberately ignored
                # for the target-year backtrace. They were CGT events, if at all,
                # in their own earlier Financial Year and do not need their own
                # cost-basis calculation merely because the associated historical
                # withdrawal is being traced to a later target-year disposal.
                # The Bitaroo statement balance already records the post-fee
                # quantity, and the historical withdrawal logic treats the
                # ~0.00010000 BTC residual as exhausted when appropriate.
                return
            return

        if kind == "bitaroo_deposit_validation":
            _, r = ev.payload
            qty = r.amount
            source = self.find_wallet_exchange_outgoing(qty, r.dt)
            if source is not None:
                # Validation is separate from movement. The linked wallet ->
                # Exchange event is what actually credits Bitaroo.
                self.validated_wallet.add(source.ref)
                return

            other = self.find_other_exchange_buy(qty, r.dt)
            if other is not None:
                # A corroborating Other Exchange purchase supplies the acquisition
                # cost basis. Create the lot here if it was not already seeded into
                # a personal wallet receipt. This keeps Bitaroo's historical replay
                # chronological instead of globally inserting old purchases before
                # the authoritative target-FY opening checkpoint.
                if other.ref not in self.seeded_other_exchange_rows:
                    original, aud_value, _source = self.other_exchange_aud_value(other)
                    trace = (
                        f"{human_date(other.dt)} {other.exchange} Buy {qb(other.btc)} BTC for "
                        f"{other.currency} {qmoney(original):,.2f} = ${qmoney(aud_value):,.2f} AUD"
                    )
                    self.record_other_exchange_acquisition(other, "Bitaroo", aud_value, trace)
                    self.seeded_other_exchange_rows.add(other.ref)
                self.matched_other_exchange_deposits.add(other.ref)
                return

            # V21 ignored an unmatched Bitaroo Deposit. That is unsafe for a
            # historical chain: the exchange statement can prove that the BTC
            # entered Bitaroo even when the corresponding source-wallet export
            # is absent. The holding pool could therefore be understated, making
            # a later Bitaroo Withdrawal appear to have no parcel behind it.
            #
            # V24 carries such a required deposit as a quantity-only reconciliation
            # lot. The lot can flow through Bitaroo -> wallet -> Bitaroo normally,
            # but no AUD cost base is invented. If it eventually reaches a taxable
            # disposal, the associated proceeds are explicitly excluded pending
            # cost-basis reconciliation.
            if self.active_bitaroo_rows is not None and id(r) not in self.active_bitaroo_rows:
                return
            rid = id(r)
            if rid not in self.unreconciled_bitaroo_deposits:
                self.unreconciled_bitaroo_deposits.add(rid)
                trace = (
                    f"{human_date(tax_date(r.dt))} Bitaroo deposit {qb(qty)} BTC; "
                    "source wallet/export not supplied, AUD cost basis unresolved"
                )
                self.add(
                    "Bitaroo",
                    self.new_lot(
                        tax_date(r.dt), qty, Decimal("0"),
                        "Unreconciled Bitaroo deposit",
                        trace,
                        basis_known=False,
                    ),
                )
                self.review.append(
                    f"{human_date(tax_date(r.dt))} Bitaroo Deposit {qb(qty)} BTC has no matching supplied source-wallet transaction. "
                    "The quantity is carried as an unreconciled historical parcel so later withdrawals/transfers can still be traced, but its AUD cost base has not been inferred."
                )
            return

        if kind == "wallet_to_exchange":
            w: WalletTx = ev.payload
            if w.ref in self.processed_wallet:
                return

            # The wallet source data can contain legitimate transfers to an exchange
            # that do not match a Bitaroo Deposit row. Those coins must still LEAVE
            # the source wallet. The old implementation simply returned here,
            # which left historical BTC sitting in the wallet and caused later
            # Bitaroo transfers to inherit exhausted old lots.
            match = match_bitaroo_deposit_row(self.reports, w)
            if match is None:
                self._take(w.sheet, w.principal, f"Wallet -> unmatched Exchange transfer", w.dt)
                fee_for_event = self.claim_wallet_fee(w)
                self.note_missing_online_fee_if_relevant(w)
                if fee_for_event > BTC_EPS:
                    if self.period_start <= w.dt <= self.period_end:
                        p = self.market_price(w.dt, "network fee on unmatched Exchange transfer")
                        self.dispose(w.sheet, fee_for_event, fee_for_event * p, w.dt, "Network fee")
                    else:
                        self._take(w.sheet, fee_for_event, "Historical network fee on unmatched Exchange transfer", w.dt)
                self.processed_wallet.add(w.ref)
                return

            transfer_qty, _, deposit_row = match
            # If the matched source wallet is explicitly external to the taxpayer,
            # this is not a taxpayer-owned wallet disposal or transfer. It is an
            # acquisition by the taxpayer into Bitaroo from another person/entity.
            # Do not attempt to consume the external wallet's lots and do not charge
            # its network fee to the taxpayer. The Bitaroo deposit is authoritative
            # for the received quantity and date, and the external receipt is given
            # a market-value cost base on that receipt date.
            transfer_dt = tax_date(deposit_row.dt)
            trace_source = self._trace_source_for_wallet_row(w)
            source_status = self.ownership.planning_wallet_status(
                w.sheet, require_ownership=True, trace_source=trace_source
            )
            if source_status == WalletOwnership.EXTERNAL:
                self.acquire_from_external_person(
                    "Bitaroo",
                    transfer_qty,
                    transfer_dt,
                    f"receipt from external wallet {w.sheet}",
                )
                self.processed_wallet.add(w.ref)
                return

            transfer_fee = self.claim_wallet_fee(w)
            self.note_missing_online_fee_if_relevant(w)
            # Use the authoritative Bitaroo Deposit date for the linked movement
            # into the exchange. The event was scheduled with the Bitaroo UTC
            # timestamp except for the narrow same-day wallet-order conflict handled
            # in build_events above.
            self.move(w.sheet, "Bitaroo", transfer_qty, transfer_dt, transfer_fee, "Wallet -> Exchange", fee_dt=w.dt)
            # If the wallet's principal is slightly larger than the matched Bitaroo
            # deposit, remove any tiny residual from the source wallet rather than
            # leaving it behind as a false historical holding. This covers exchange-
            # side fee/rounding differences that are not represented as a separate
            # Bitaroo Deposit amount.
            # Both CSV and ODS principals represent net outputs. move() has
            # already charged the transaction-level miner fee separately once.
            residual = w.principal - transfer_qty
            if residual > BTC_EPS:
                self._take(w.sheet, residual, "Residual from matched Exchange transfer", w.dt)
            self.processed_wallet.add(w.ref)
            return

        if kind == "wallet_out":
            w: WalletTx = ev.payload
            sale=getattr(self,'other_sale_transfers',{}).get(('wallet',w.ref))
            if sale is not None:
                if w.ref not in self.processed_wallet:
                    self.move(w.sheet,sale.exchange,w.principal,w.dt,fee=self.claim_wallet_fee(w),desc='Transfer funding supplied exchange sale')
                    self.processed_wallet.add(w.ref)
                return
            trace_source = self._trace_source_for_wallet_row(w)
            if w.ref in self.processed_wallet:
                return
            dest_match = find_loaded_wallet_counterparty(self.wallets, w.sheet, w)
            dest_idx = dest_match[1] if dest_match is not None else None
            dest = self.ownership.ensure_wallet_identity(self.wallets, dest_match[0], trace_source=trace_source) if dest_match is not None else None
            if not dest:
                # The destination is genuinely unresolved. Do not silently assume
                # that an address which is not present in the supplied wallet set
                # belongs to somebody else. When this wallet row is part of the
                # active target-year trace, ask the taxpayer whether the destination
                # address belongs to them. A "No" answer creates the ownership
                # boundary; a "Yes" answer deliberately stops the calculation with
                # a missing-wallet error because the corresponding taxpayer-owned
                # wallet history was not supplied.
                destination_owned = self.ownership.decide_external_address(w, trace_source=trace_source)
                # decide_external_address() raises MissingWalletDataError when the
                # answer is Yes, so reaching this point means the answer was No.
                fee_for_event = self.claim_wallet_fee(w)
                self.note_missing_online_fee_if_relevant(w)
                if self.period_start <= w.dt <= self.period_end:
                    p = self.market_price(w.dt, f"market value of BTC transferred from {w.sheet} to external destination")
                    self.dispose(w.sheet, w.principal, w.principal * p, w.dt, "Transfer to external destination (taxable disposal)")
                    if fee_for_event > BTC_EPS:
                        pf = self.market_price(w.dt, "network fee on transfer to external destination")
                        self.dispose(w.sheet, fee_for_event, fee_for_event * pf, w.dt, "Network fee")
                else:
                    self._take(w.sheet, w.principal, "Historical transfer to external destination", w.dt)
                    if fee_for_event > BTC_EPS:
                        self._take(w.sheet, fee_for_event, "Historical network fee to external destination", w.dt)
                self.processed_wallet.add(w.ref)
                return
            # Use deferred planning status while reconstructing historical wallet
            # transfers. An ODS-only wallet can be used without an ownership
            # decision during provenance-only replay, but an active destination
            # affects whether the transfer is internal or taxable and therefore
            # requires an explicit ownership decision.
            dest_dt = self.wallets[dest][dest_idx].dt if dest_idx is not None else w.dt
            require = self.ownership.ownership_required_for_row(dest, dest_dt, self.period_start, self.period_end)
            dest_status = self.ownership.planning_wallet_status(dest, require_ownership=require, trace_source=trace_source)
            if dest_status == WalletOwnership.EXTERNAL:
                if self.period_start <= w.dt <= self.period_end:
                    p = self.market_price(w.dt, f"market value of BTC transferred from {w.sheet} to external wallet {dest}")
                    self.dispose(w.sheet, w.principal, w.principal * p, w.dt,
                                 f"Transfer to {dest} (external wallet — taxable disposal)")
                    fee_for_event = self.claim_wallet_fee(w)
                    self.note_missing_online_fee_if_relevant(w)
                    if fee_for_event > BTC_EPS:
                        pf = self.market_price(w.dt, "network fee on external wallet transfer")
                        self.dispose(w.sheet, fee_for_event, fee_for_event * pf, w.dt, "Network fee")
                else:
                    self._take(w.sheet, w.principal, f"Historical transfer to external wallet {dest}", w.dt)
                    fee_for_event = self.claim_wallet_fee(w)
                    self.note_missing_online_fee_if_relevant(w)
                    if fee_for_event > BTC_EPS:
                        self._take(w.sheet, fee_for_event, f"Historical network fee to external wallet {dest}", w.dt)
                self.processed_wallet.add(w.ref)
                return

            fee_for_event = self.claim_wallet_fee(w)
            self.note_missing_online_fee_if_relevant(w)
            self.move(w.sheet, dest, w.principal, w.dt, fee_for_event, "Wallet transfer")
            self.processed_wallet.add(w.ref)
            return

    def reset_bitaroo_to_target_opening(self):
        """Reconcile the exchange ledger to the target FY opening statement balance.

        Historical dependency replay can intentionally be incomplete around old
        statement exceptions. The target FY BTC statement, however, gives an
        authoritative opening quantity. When an explicit reconstructed opening
        cost-basis lot exists, use it; otherwise preserve historical lots only
        when their calculated quantity exactly agrees with the statement.
        """
        boundary = local_to_utc(datetime.combine(self.period_start, datetime.min.time()))
        prior = [r for r in self.reports.btc_rows if utc_time(r.dt) < boundary]
        # Statements have UTC boundaries; the tax year starts at local midnight.
        # Use the last authoritative balance before THAT instant so a June-30
        # UTC trade occurring on July-1 locally is processed as a new acquisition.
        target_start = prior[-1].balance if prior else self.reports.start_bal.get(self.fy_start)
        if target_start is None:
            return
        target_start = Decimal(target_start)
        current = self.qty("Bitaroo")

        if target_start <= BTC_EPS:
            self.holdings["Bitaroo"] = []
            return

        if abs(current - target_start) <= Decimal("0.00000002"):
            return

        opening = self.reports.opening_lots.get(self.fy_start, [])
        if opening:
            self.holdings["Bitaroo"] = []
            for acquired, qty, cost, source in opening:
                trace = f"{human_date(acquired)} Bitaroo opening balance {qb(qty)} BTC for ${qmoney(cost):,.2f}"
                self.add("Bitaroo", self.new_lot(acquired, qty, cost, source, trace))
            return

        self.review.append(
            f"Target FY {fmt_fy(self.fy_start)} Bitaroo opening balance is {qb(target_start)} BTC, "
            f"but the traced historical ledger contains {qb(current)} BTC and no explicit opening cost-basis lot was reconstructed. "
            f"Older history may require manual reconciliation."
        )
        self.holdings["Bitaroo"] = []

    def run(self):
        # IMPORTANT: do not seed a later FY opening balance before historical
        # replay. reset_bitaroo_to_target_opening() applies the authoritative
        # target-year opening checkpoint only after all pre-target events are
        # reconstructed.
        # Seed only acquisitions that pre-date the target FY during the historical replay.
        # Target-FY acquisitions are introduced on their own dates below.
        self.seed_other_exchange_acquisitions(through_date=self.period_start - timedelta(days=1))
        all_events = self.build_events()
        scoring_prefix=getattr(self,'scoring_prefix',None)
        resumed=scoring_prefix.prepare(self,all_events) if scoring_prefix else False
        saved_prefix=resumed

        # First replay historical dependencies to reconstruct the wallet chains.
        # Then reset the Bitaroo exchange to the authoritative target-FY opening
        # balance before processing the actual tax-year disposals. This prevents
        # unrelated or unresolved historical exchange residue from contaminating
        # the target year's cost-basis pool.
        for ev in all_events:
            if not resumed and tax_date(ev.when) < self.period_start:
                self.process_event(ev)
        if not resumed:self.reset_bitaroo_to_target_opening()
        for ev in all_events:
            # Process only events inside the requested Financial Year after the
            # historical pre-opening replay. Future FY events must never enter
            # the target-year calculation or generate review items.
            if self.period_start <= tax_date(ev.when) <= self.period_end:
                if resumed and utc_time(ev.when)<scoring_prefix.boundary:continue
                if scoring_prefix and not saved_prefix and utc_time(ev.when)>=scoring_prefix.boundary:
                    scoring_prefix.save(self);saved_prefix=True
                # Introduce any Other Exchange acquisitions dated on/before this
                # target-year event at their actual ledger date.
                self.seed_other_exchange_acquisitions(through_date=tax_date(ev.when), through_instant=utc_time(ev.when))
                self.process_event(ev)

        if scoring_prefix and not saved_prefix:scoring_prefix.save(self)

        # Detect Other Exchange buys that were not corroborated by either a matching
        # Bitaroo Deposit or an exact BTC/date wallet receipt. The lot is
        # still retained because the Other Exchange spreadsheet is the source of the
        # acquisition, but the destination/address needs review.
        for row in self.other_exchange_rows:
            if row.dt > self.period_end or not row.buy:
                continue
            if row.ref in self.matched_other_exchange_deposits:
                continue
            corroborating = [w for values in self.wallets.values() for w in values
                if w.incoming and w.dt <= self.period_end
                and self.ownership.wallet_statuses.get(w.sheet) == WalletOwnership.PERSONAL
                and match_other_exchange_buy_to_wallet_receipt(self.other_exchange_rows, w, max_date=self.period_end) is row]
            if len(corroborating) == 1:
                continue
            if any(
                note.startswith(f"{human_date(row.dt)} {row.exchange} buy {qb(row.btc)} BTC matched to ")
                for note in self.other_exchange_match_notes
            ):
                continue

            # A historical Other Exchange buy can be corroborated directly by a
            # Bitaroo Deposit even when that old Bitaroo row was not part of the
            # target-year dependency graph.  That is exactly what happens when the
            # purchased BTC entered Bitaroo and was then fully withdrawn before the
            # requested FY: the dependency planner correctly does not replay that
            # exhausted old deposit, so its validation event never gets a chance to
            # populate matched_other_exchange_deposits.  Do not report such a row as
            # an unmatched destination merely because the corroborating historical
            # deposit was outside the active dependency slice.
            destination = self.other_exchange_wallets.get(row.ref, row.exchange)
            if destination == "Bitaroo" and self._unique_bitaroo_deposit_for_other_exchange(row) is not None:
                continue

            if row.dt < self.period_start:
                marker = f"{human_date(row.dt)} {row.exchange} Buy"
                if not any(marker in note for event in self.events for note in event.notes):
                    continue
            self.review.append(
                f"{human_date(row.dt)} {row.exchange} buy {qb(row.btc)} BTC for "
                f"{row.currency} {qmoney(abs(row.fiat)):,.2f} was not matched to a Bitaroo Deposit or a wallet receipt. "
                "The acquisition lot was still recorded from the Other Exchange spreadsheet; verify the destination address/wallet."
            )

        self.events.sort(key=lambda e: (utc_time(e.date), e.description, e.bucket, e.lot_source))

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_report(path: str, engine: Engine):
    # Every disposal is written individually. We do not combine separate sales
    # merely because they happened on the same day or were supplied from the
    # same holding pool. A single disposal may legitimately generate two events
    # when one part was held >=12 months and another part was held <12 months.
    evs = [e for e in engine.events if engine.period_start <= e.date <= engine.period_end]
    evs.sort(key=lambda e: (utc_time(e.date), e.description, e.bucket, e.lot_source))

    short_gains = sum((e.gain_loss for e in evs if e.bucket == "held < 12 months" and e.gain_loss > 0), Decimal("0"))
    short_losses = -sum((e.gain_loss for e in evs if e.bucket == "held < 12 months" and e.gain_loss < 0), Decimal("0"))
    long_gains = sum((e.gain_loss for e in evs if e.bucket == "held >= 12 months" and e.gain_loss > 0), Decimal("0"))
    long_losses = -sum((e.gain_loss for e in evs if e.bucket == "held >= 12 months" and e.gain_loss < 0), Decimal("0"))
    # The annual allocation is authoritative for all report totals. Parcel-level
    # indicative discounts must never be subtracted from an already-net result.
    summary = annual_summary(evs, CARRIED_LOSSES)

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"BITCOIN CGT WORKING PAPER — {engine.taxpayer} — FY {fmt_fy(engine.fy_start)}\n")
        f.write("Generated from Bitaroo AUD/BTC statements, Bitcoin Core wallet descriptor-derived records, historical wallet ODS, and the owned tabs of the Other Exchanges ODS data when supplied.\n")
        f.write("V107: CSV local timestamps converted to UTC using OS historical timezone rules (or --timezone). ODS Date (UTC)/Time (UTC) cells are read as UTC. Matching requires timestamps and uses the configured elapsed-time tolerance. Financial years use the local tax calendar.\n")
        f.write("V107 prices: BTC valuations use direct BTC/AUD trades or completed BTC/AUD trading minutes; no USD bridge. Minute prices disclose their availability bounds and do not supply exact tick times. Automatic FX uses fully available historical minute quotes, with source bar labels and conservative availability bounds; exact tick times are not supplied by these archives. Weekend FX uses the immediately preceding Friday's last available quote, disclosing each conversion leg and its age. BTC trade freshness remains separately enforced.\n")
        f.write("Timestamped valuations use the latest observed market trade/rate at or before the instant, with a maximum quote age of 60 minutes by default; observed timestamps are disclosed. Date-only valuations use daily close.\n")
        wall = time.perf_counter() - RUN_STARTED
        local_work = max(0.0, wall - NETWORK_SECONDS - PROMPT_SECONDS)
        f.write(f"PERFORMANCE: {NETWORK_CALLS} HTTP attempts, {NETWORK_SECONDS:.2f}s HTTP/provider waiting time, {CACHE_HITS} disk cache hits. {THROTTLE_SECONDS:.2f}s provider-rate-limit pacing (included in HTTP time). Approx. {local_work:.2f}s local processing/file IO; {PROMPT_SECONDS:.2f}s answering prompts. No arbitrary processing sleeps.\n")
        f.write("For Bitcoin Core outgoing rows, on-chain transaction outputs and transaction-level miner fees are reconciled from public Bitcoin transaction data where a txid is available. The miner fee is treated as a separate CGT disposal and is charged only once per transaction, even when a transaction has multiple send outputs.\n")
        f.write("A wallet-to-wallet transfer retains its traced cost base only when the destination is confirmed as belonging to the taxpayer. A transfer to another person's wallet is treated as a disposal at market value; receipt from another person's wallet is treated as a new acquisition at market value.\n")
        f.write(f"Matched fiat Bitaroo disposal fees deducted from proceeds: {INCLUDE_FIAT_DISPOSAL_FEES}. Default False retains the V93 convention; --include-fiat-disposal-fees enables deduction.\n")
        f.write(f"Prior-year losses supplied: ${qmoney(CARRIED_LOSSES):,.2f}. Figures cover this Bitcoin ledger; other assets/income are not inferred.\n")
        f.write("Each disposal is calculated separately; no distinct sales are combined.\n\n")

        for n, e in enumerate(evs, start=1):
            f.write(f"CGT EVENT {n}")
            if "fee" in e.description.lower() or "network fee" in e.description.lower():
                f.write(".FEE")
            if e.bucket == "held >= 12 months":
                f.write(" [Part held longer than 12 months]")
            elif e.bucket == "held < 12 months":
                f.write(" [Part held less than 12 months]")
            elif e.bucket.startswith("pre-reform deferred"):
                f.write(" [Pre-1-Jul-2027 deferred leg]")
            elif e.bucket == "post-reform":
                f.write(" [Post-1-Jul-2027 leg]")
            f.write("\n-----------\n")

            if e.sale_qty and e.pool_qty:
                pct = e.sale_qty / e.pool_qty * Decimal("100")
                if "network fee" in e.description.lower():
                    f.write(f"{human_date(e.date)} Transaction fee {qb(e.btc)} out of {qb(e.pool_qty)} ({pct:.12f}%)\n")
                else:
                    total_proceeds = e.sale_proceeds_total if e.sale_proceeds_total else e.proceeds
                    f.write(f"{human_date(e.date)} {e.description.split(' for $')[0]} out of {qb(e.pool_qty)} ({pct:.12f}%) for ${qmoney(total_proceeds):,.2f}\n")
                    if e.bucket in {"held >= 12 months", "held < 12 months"}:
                        part_pct = e.btc / e.sale_qty * Decimal("100") if e.sale_qty else Decimal("100")
                        f.write(f"    Part of sale in this holding bucket = {qb(e.btc)} {e.asset_unit} / {qb(e.sale_qty)} {e.asset_unit} = {part_pct:.12f}% of the sale.\n")
            else:
                f.write(f"{human_date(e.date)} {e.description}\n")

            formula_lines = []
            trace_lines = []
            other_notes = []
            for note in e.notes:
                if note.startswith("TRACE:"):
                    trace_lines.append(note)
                elif any(note.startswith(prefix) for prefix in (
                    "SOURCE LOT:", "SALE ALLOCATION:", "COST BASE MATH:",
                    "PROCEEDS MATH:", "HOLDING PERIOD:",
                    "50% CGT DISCOUNT MATH:", "POST-REFORM COST BASE:"
                )):
                    formula_lines.append(note)
                else:
                    other_notes.append(note)

            for line in formula_lines:
                f.write(f"    {line}\n")
            if formula_lines:
                f.write(f"    EVENT PROCEEDS = ${qmoney(e.proceeds):,.2f}\n")
                f.write(f"    EVENT COST BASE = ${qmoney(e.cost):,.2f}\n")
            gl = qmoney(e.gain_loss)
            f.write(f"    THEREFORE ${qmoney(e.proceeds):,.2f} - ${qmoney(e.cost):,.2f} = {'Gain' if gl >= 0 else 'Loss'} ${abs(gl):,.2f}\n")
            if e.discounted_gain > 0:
                f.write(f"    Indicative 50% reduction on positive source parcels before annual loss allocation = ${qmoney(e.discounted_gain):,.2f}\n")
            for nline in other_notes:
                f.write(f"    NOTE: {nline}\n")
            for tline in trace_lines:
                f.write(f"    {tline}\n")
            if e.lot_source:
                f.write(f"    LOT SOURCE: {e.lot_source}\n")
            f.write("\n")

        f.write("/------\\\n|TOTALS|\n\\------/\n")
        f.write(f"Total Events Count = {len(evs)}\n")
        f.write(f"Total gains held less than 12 months = ${qmoney(short_gains):,.2f}\n")
        f.write(f"Total losses held less than 12 months = ${qmoney(short_losses):,.2f}\n")
        f.write(f"Sum of gains and losses held less than 12 months = ${qmoney(short_gains-short_losses):,.2f}\n")
        f.write(f"Total gains held longer than 12 months = ${qmoney(long_gains):,.2f}\n")
        f.write(f"Total losses held longer than 12 months = ${qmoney(long_losses):,.2f}\n")
        f.write(f"Sum of gains and losses held longer than 12 months = ${qmoney(long_gains-long_losses):,.2f}\n")
        f.write(f"Total sum held longer than 12 months with CGT 50% Discount = ${qmoney(summary['discounted_long_gain']):,.2f}\n")
        f.write(f"Grand total without CGT Discount applied = ${qmoney(summary['net_result_before_discount']):,.2f}\n")
        f.write(f"Grand total with CGT Discount applied = ${qmoney(summary['net_result_after_discount']):,.2f}\n")

        if engine.other_exchange_rows:
            f.write("\nOTHER EXCHANGE TRANSACTIONS\n")
            for row in engine.other_exchange_rows:
                if row.dt > engine.period_end:
                    continue
                _original, aud_value, _source = engine.other_exchange_aud_value(row)
                direction = "BUY" if row.buy else "SELL"
                f.write(
                    f"{human_date(row.dt)} {row.exchange} {direction}: "
                    f"Other asset {row.fiat:+f} {row.currency}; BTC {row.btc:+,.8f}; "
                    f"AUD equivalent ${qmoney(aud_value):,.2f}; destination/source wallet {engine.other_exchange_wallets.get(row.ref, row.exchange)}.\n"
                )
                for label, amount, unit in row.fees:
                    f.write(f"    {label}: {amount:f} {unit}.\n")

        if engine.other_exchange_match_notes:
            f.write("\nOTHER EXCHANGE / BITAROO MATCH NOTES\n")
            for note in dict.fromkeys(engine.other_exchange_match_notes):
                f.write(f"- {note}\n")

        if engine.fx_usage:
            f.write("\nFOREIGN EXCHANGE LOOKUPS USED\n")
            seen_fx = set()
            for dt, currency, rate, _aud_value, source in sorted(engine.fx_usage, key=lambda x: (utc_time(x[0]), x[1], x[2])):
                key = (display_time(dt), currency, rate, source)
                if key in seen_fx:
                    continue
                seen_fx.add(key)
                rate_display = D(rate).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                f.write(
                    f"{display_time(dt)} {currency}/AUD = {rate_display:,.8f} AUD per {currency}; "
                    f"source: {source}.\n"
                )

        if engine.price_usage:
            f.write("\nPRICE LOOKUPS USED\n")
            seen = set()
            for dt, context, price, source in sorted(engine.price_usage, key=lambda x: (utc_time(x[0]), x[1])):
                key = (display_time(dt), price, source)
                if key in seen:
                    continue
                seen.add(key)
                f.write(f"{display_time(dt)} BTC/AUD = ${qmoney(price):,.2f} — {source}\n")
                f.write(f"    Used for: {context}\n")

        if engine.period_end >= REFORM_DATE:
            f.write("\nPOST-REFORM TAX CALCULATION NOTES\n")
            f.write(f"Post-reform minimum-tax gain before personal deductions/exemptions = ${qmoney(summary['minimum_tax_gain']):,.2f}\n")
            if TAX_CONTEXT is not None:
                gap = minimum_tax_gap(summary['minimum_tax_gain'], **TAX_CONTEXT)
                f.write(f"Division 119 additional minimum tax using supplied whole-return basic tax context = ${gap:,.0f}\n")
            else:
                f.write("Division 119 additional income tax pending whole-return basic tax/exemption details (--tax-context).\n")

        cpi_used = getattr(engine, '_cpi_cache', {}) or CPI_VALUES
        if engine.period_end >= REFORM_DATE and cpi_used:
            f.write("\nCPI INPUTS (quarterly All Groups, weighted eight capital cities)\n")
            for quarter, index in sorted(cpi_used.items()):
                f.write(f"{quarter}: {index}; {'user-supplied consistent-base file' if CPI_VALUES else 'ABS CPI 2.0.0 API'}\n")

        if engine.ownership.report_wallets:
            f.write("\nWallet ownership decisions used by the trace:\n")
            for wallet in sorted(engine.ownership.report_wallets):
                status = WalletOwnership._normalise_status(engine.ownership.wallet_statuses[wallet])
                if status == WalletOwnership.PERSONAL:
                    text = "owned by taxpayer"
                else:
                    text = "not owned by taxpayer — treated as a CGT disposal/acquisition boundary"
                f.write(f"- {wallet}: {text}\n")

# ---------------------------------------------------------------------------
# Interactive loading
# ---------------------------------------------------------------------------





def _parse_statement_date(text: str) -> date:
    cleaned = text.replace(",", "").strip()
    for fmt in ("%d %B %Y", "%B %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Could not parse Bitaroo statement date: {text}")


def inspect_bitaroo_statement(path: Path) -> Tuple[str, int, date, date]:
    """Identify Bitaroo statement side and covered FY/range without relying on filename."""
    reader = PdfReader(str(path))
    full = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
    clean = clean_text(full)

    side_matches = []
    if re.search(r"\bAUD\s+Statement\b", clean, re.I):
        side_matches.append("AUD")
    if re.search(r"\bBTC\s+Statement\b", clean, re.I):
        side_matches.append("BTC")
    if len(side_matches) != 1:
        raise ValueError(f"Could not determine whether {path.name} is a Bitaroo AUD or BTC statement")

    m = RANGE_RE.search(clean)
    if not m:
        raise ValueError(f"Could not determine the date range of Bitaroo statement {path.name}")
    start = _parse_statement_date(m.group(1))
    end = _parse_statement_date(m.group(2))

    # Bitaroo annual/YTD statements for an Australian FY should begin on 1 July.
    if (start.month, start.day) != (7, 1):
        raise ValueError(
            f"Bitaroo statement {path.name} does not begin on 1 July ({start.isoformat()}); "
            "the statement cannot be assigned reliably to an Australian Financial Year."
        )
    if end < start:
        raise ValueError(f"Bitaroo statement {path.name} has an end date before its start date")

    return side_matches[0], start.year, start, end



def index_bitaroo_statement_directory(directory: str):
    """Index Bitaroo PDFs by Financial Year and statement side without loading ledger rows."""
    directory_path = Path(directory).expanduser()
    if not directory_path.is_dir():
        raise ValueError(f"Bitaroo statement directory does not exist: {directory_path}")
    pdf_paths = sorted(
        (p for p in directory_path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: p.name.lower(),
    )
    if not pdf_paths:
        raise ValueError(f"No Bitaroo PDF files were found in the statement directory: {directory_path}")

    grouped: Dict[int, Dict[str, List[Tuple[Path, str, int, date, date]]]] = defaultdict(lambda: {"AUD": [], "BTC": []})
    ignored = []
    for path in pdf_paths:
        try:
            side, fy, start, end = inspect_bitaroo_statement(path)
        except Exception as exc:
            ignored.append((path.name, str(exc)))
            continue
        grouped[fy][side].append((path, side, fy, start, end))

    if not grouped:
        detail = "; ".join(f"{name}: {reason}" for name, reason in ignored[:5])
        raise ValueError(f"No valid Bitaroo AUD/BTC statements could be identified in {directory_path}. {detail}")
    return grouped


def select_bitaroo_statement_pair(grouped, fy: int, target_fy: int):
    """Select the AUD/BTC pair for a FY.

    Target FY and future FYs: use the most recent statement on each side; it may
    be incomplete/YTD because it is not a historical dependency for the requested
    tax year.
    Past FYs: require a complete 1-Jul to 30-Jun statement on both sides.
    """
    selections = {}
    for side in ("AUD", "BTC"):
        candidates = list(grouped.get(fy, {}).get(side, []))
        if not candidates:
            raise ValueError(
                f"Missing Bitaroo {side} data from {fmt_fy(fy)} Financial Year. "
                "Ensure all historical Bitaroo Statements are provided so that tax can be calculated"
            )
        if fy >= target_fy:
            # The requested FY may be YTD/partial, and statements from future
            # FYs may also be YTD/partial. Future statements are loaded because
            # the directory is preloaded up front, but they are never treated as
            # historical evidence needed to establish the target year's opening
            # position. Therefore, do not require a future statement to run all
            # the way through 30 June. Prefer the most recent supplied statement.
            selections[side] = sorted(
                candidates,
                key=lambda x: (x[4], x[0].name.lower()),
                reverse=True,
            )[0]
        else:
            # Any FY before the requested tax year is historical input. It must
            # be a complete Australian Financial Year so the historical Bitaroo
            # chain can be reconstructed reliably.
            complete = [
                x for x in candidates
                if x[3] == date(fy, 7, 1) and x[4] == date(fy + 1, 6, 30)
            ]
            if not complete:
                raise ValueError(
                    f"Incomplete Bitaroo {side} data from {fmt_fy(fy)} Financial Year. "
                    "Ensure all historical Bitaroo Statements are provided so that tax can be calculated"
                )
            selections[side] = sorted(complete, key=lambda x: x[0].name.lower())[0]

    aud = selections["AUD"]
    btc = selections["BTC"]
    if aud[3] != btc[3] or aud[4] != btc[4]:
        raise ValueError(
            f"Bitaroo AUD and BTC statements for FY {fmt_fy(fy)} do not cover the same date range: "
            f"AUD {aud[3].strftime('%d %b %Y')} to {aud[4].strftime('%d %b %Y')}; "
            f"BTC {btc[3].strftime('%d %b %Y')} to {btc[4].strftime('%d %b %Y')}"
        )
    return str(aud[0]), str(btc[0])


def load_bitaroo_reports_from_directory(reports: ReportSet, directory: str, target_fy: int):
    """Load every Bitaroo FY available in the selected directory up front.

    The directory itself determines the historical range. There is deliberately
    no hard-coded earliest FY. The target FY is allowed to be a YTD/partial
    statement; every FY before the target is required to be a complete
    1-Jul to 30-Jun statement so that historical lots can be replayed without
    asking for PDFs later during the tax trace. Future FY statements are allowed
    to be partial/YTD and are not used as historical evidence for the target FY.
    """
    grouped = index_bitaroo_statement_directory(directory)

    # Load the target FY first so that, when the user has supplied a target-year
    # YTD statement alongside complete historical statements, the target report
    # is unambiguously the selected current-period statement. Then load every
    # other indexed FY. All statement data is resident in ReportSet before any
    # wallet ownership prompts or historical dependency tracing begin.
    ordered_fys = sorted(grouped)
    if target_fy not in grouped:
        raise ValueError(
            f"Missing Bitaroo AUD/BTC data for requested target Financial Year {fmt_fy(target_fy)}. "
            "Ensure the target FY AUD and BTC statements are present in the selected directory."
        )

    load_order = [target_fy] + [fy for fy in ordered_fys if fy != target_fy]
    for fy in load_order:
        aud, btc = select_bitaroo_statement_pair(grouped, fy, target_fy)
        reports.add(fy, aud, btc)

    # Only reconstruct opening lots after the complete set of supplied Bitaroo
    # statements is loaded. This lets a later FY's non-zero opening balance be
    # traced back to the prior FY's 30-Jun acquisition without a second load pass.
    reports.reconstruct_opening_lots()
    return grouped



def trace_history(reports: ReportSet, target_fy: int, wallets: Dict[str, List[WalletTx]], ownership: WalletOwnership,
                  other_exchange_rows: Optional[List[OtherExchangeRow]] = None) -> "HistoryPlanner":
    """Trace the Exchange graph, then perform the Wallet Tracer."""
    planner = HistoryPlanner(wallets, target_fy, ownership, other_exchange_rows, reports)
    planner.seed(reports)

    # Wallet metadata is independent of the dependency graph. Build it once instead
    # of recomputing the same transaction relationships on every convergence pass.
    refresh_wallet_metadata(wallets, reports)

    def close_wallet_to_bitaroo(auxiliary: bool = False) -> None:
        for wallet, rows in wallets.items():
            for w in rows:
                if w.ref not in planner.required_wallet_rows or not w.outgoing:
                    continue
                if auxiliary and w.ref in planner.primary_required_wallet_rows:
                    continue
                if find_loaded_wallet_counterparty(wallets, wallet, w) is not None:
                    continue
                match = match_bitaroo_deposit_row(reports, w)
                if match is None:
                    continue
                qty, _, dep = match
                planner.enqueue_bitaroo(
                    planner.fy_for_date(tax_date(dep.dt)),
                    dep.dt,
                    qty,
                    "deposit",
                    trace_source=planner.wallet_row_trace_source.get(
                        w.ref, "Exchange Tracer"
                    ),
                )

    # Phase 1: Exchange Tracer.
    while True:
        before = (
            len(planner.required_wallet_rows),
            len(planner.required_bitaroo_keys),
            len(planner.required_bitaroo_rows),
        )
        planner.process_wallet_queue()
        close_wallet_to_bitaroo()
        planner.process_bitaroo_queue(reports)
        after = (
            len(planner.required_wallet_rows),
            len(planner.required_bitaroo_keys),
            len(planner.required_bitaroo_rows),
        )
        if after == before:
            break

    planner.primary_required_wallet_rows = set(planner.required_wallet_rows)
    planner.primary_required_bitaroo_rows = set(planner.required_bitaroo_rows)

    # Phase 2: Wallet Tracer. Only rows not already discovered by the
    # primary trace are introduced as auxiliary dependencies.
    while True:
        before = (
            len(planner.required_wallet_rows),
            len(planner.required_bitaroo_keys),
            len(planner.required_bitaroo_rows),
        )
        planner.reconcile_target_year_wallet_activity(trace_source="Wallet Tracer")
        planner.process_wallet_queue()
        close_wallet_to_bitaroo(auxiliary=True)
        planner.process_bitaroo_queue(reports)
        missing_years = planner.required_years_after(reports)
        if missing_years:
            years = ", ".join(fmt_fy(y) for y in missing_years)
            raise ValueError(
                f"Bitaroo history required for FY {years} was not loaded. "
                "Ensure the corresponding complete Bitaroo AUD and BTC statements "
                "are present in the selected Bitaroo directory."
            )
        after = (
            len(planner.required_wallet_rows),
            len(planner.required_bitaroo_keys),
            len(planner.required_bitaroo_rows),
        )
        if after == before:
            return planner


def configure():
    global LOCAL_ZONE, HTTP_TIMEOUT, CACHE_PATH, QUOTE_MAX_AGE_SECONDS, TAX_CONTEXT, CARRIED_LOSSES, INCLUDE_FIAT_DISPOSAL_FEES, MATCH_HOURS
    from matching_policy import WINDOWS, DEFAULT_WINDOW, matching_window
    PRICE_MESSAGES_SHOWN.clear()
    parser = argparse.ArgumentParser(description='Bitcoin CGT Generator V107. Interactive input/ownership; automatic auditable valuations.')
    parser.add_argument('--timezone', help='IANA timezone of descriptor-derived record and local tax calendar, e.g. Australia/Sydney; default OS timezone')
    parser.add_argument('--quotes', help='Verified intraday CSV: currency,transaction_utc,observed_utc,aud_per_unit,source')
    parser.add_argument('--tax-context', help='JSON: basic_tax,basic_tax_without_gain,deductible_gifts,deductible_conservation,exempt_payment; whole-return tax context for Division 119')
    parser.add_argument('--include-fiat-disposal-fees', action='store_true', help='Deduct matched AUD Bitaroo sell fees from proceeds; default retains V93 fee convention')
    parser.add_argument('--carried-losses', default='0', help='Unapplied prior-year capital losses in AUD; default 0')
    parser.add_argument('--cpi', help='Verified ABS quarterly CPI CSV: quarter,index')
    parser.add_argument('--cache', default=str(CACHE_PATH), help='Persistent HTTP cache path (contains public transaction data)')
    parser.add_argument('--timeout', type=int, default=12, help='HTTP timeout seconds per attempt')
    parser.add_argument('--quote-max-age', type=int, default=3600, help='Maximum quote age in seconds; FX weekend closures use the preceding Friday quote with disclosed age')
    parser.add_argument("--match-window", choices=tuple(WINDOWS), default=DEFAULT_WINDOW, help="Elapsed-time tolerance for all transaction matching; default week")
    args = parser.parse_args()
    MATCH_HOURS = matching_window(args.match_window)["hours"]
    INCLUDE_FIAT_DISPOSAL_FEES = args.include_fiat_disposal_fees
    CARRIED_LOSSES = D(args.carried_losses)
    if not CARRIED_LOSSES.is_finite() or CARRIED_LOSSES < 0:
        raise ValueError('Carried losses must be finite and non-negative')
    if args.tax_context:
        TAX_CONTEXT = json.loads(Path(args.tax_context).read_text())
        minimum_tax_gap(Decimal('0'), **TAX_CONTEXT)
    if args.timezone:
        LOCAL_ZONE = ZoneInfo(args.timezone)
    HTTP_TIMEOUT = args.timeout
    if not 1 <= HTTP_TIMEOUT <= 120 or not 0 <= args.quote_max_age <= 3600:
        raise ValueError('Timeout must be 1..120 seconds; quote max age 0..3600 seconds')
    QUOTE_MAX_AGE_SECONDS = args.quote_max_age
    CACHE_PATH = Path(args.cache).expanduser()
    def aware(text):
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('Supplementary timestamps must include an explicit timezone offset')
        return value.astimezone(UTC)
    if args.quotes:
        with open(args.quotes, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                key = (r['currency'].upper(), aware(r['transaction_utc']))
                value = (aware(r['observed_utc']), D(r['aud_per_unit']), r['source'])
                if not r['source'].strip() or (key in INTRADAY.imported and INTRADAY.imported[key] != value):
                    raise ValueError('Missing source or conflicting duplicate quote')
                INTRADAY.imported[key] = value
    if args.cpi:
        with open(args.cpi, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f):
                value = D(r['index'])
                if not re.fullmatch(r'\d{4}Q[1-4]', r['quarter']) or not value.is_finite() or value <= 0:
                    raise ValueError('Invalid CPI record')
                CPI_VALUES[r['quarter']] = value
    print('Timezone:', args.timezone or 'operating system local timezone', '(historical DST rules)')




