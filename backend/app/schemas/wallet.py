"""Wallet, top-up and payment-settings schemas."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import TopUpMethod, TopUpStatus, WalletTxKind

# Networks a crypto wallet may be declared on. Each has its own address
# format; a wrong-network address is the classic way crypto payments are lost,
# so the format is checked when the operator saves it.
CRYPTO_NETWORKS: dict[str, re.Pattern[str]] = {
    # Tron: base58, starts with T, 34 characters.
    "TRC20": re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$"),
    # EVM chains: 0x + 40 hex.
    "BEP20": re.compile(r"^0x[0-9a-fA-F]{40}$"),
    "ERC20": re.compile(r"^0x[0-9a-fA-F]{40}$"),
    # TON: user-friendly base64url (48 chars) or raw "0:<64 hex>".
    "TON": re.compile(r"^([A-Za-z0-9_-]{48}|-?\d:[0-9a-fA-F]{64})$"),
}
CRYPTO_ASSETS = {"USDT", "TRX", "TON", "USDC"}

# Transaction hashes: 64 hex (Tron), 0x + 64 hex (EVM), or TON's 64 hex /
# 44-char base64. Checked loosely: the admin verifies it on the chain anyway,
# this only catches pasting an address or a URL by mistake.
_TX_HASH = re.compile(r"^(0x)?[0-9a-fA-F]{64}$|^[A-Za-z0-9+/_=-]{43,44}$")
_TRACKING = re.compile(r"^[0-9A-Za-z-]{4,40}$")


def luhn_ok(number: str) -> bool:
    """Iranian bank cards (Shetab) are Luhn-valid 16-digit numbers."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ------------------------------------------------------------------ settings
class CardSettings(BaseModel):
    enabled: bool = False
    number: str = ""
    holder: str = Field(default="", max_length=80)
    bank: str = Field(default="", max_length=60)
    instructions: str = Field(default="", max_length=500)

    @field_validator("number")
    @classmethod
    def _number(cls, v: str) -> str:
        digits = re.sub(r"[\s-]", "", v)
        # Persian and Arabic-Indic digits typed on a phone keyboard.
        digits = digits.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        if digits == "":
            return ""
        if not re.fullmatch(r"\d{16}", digits) or not luhn_ok(digits):
            raise ValueError("Card number must be a valid 16-digit card")
        return digits

    @model_validator(mode="after")
    def _complete_when_enabled(self):
        if self.enabled and (not self.number or not self.holder.strip()):
            raise ValueError("An enabled card needs a number and the holder's name")
        return self


class CryptoWallet(BaseModel):
    network: str
    asset: str = "USDT"
    address: str
    # Toman per one unit of the asset. Set by the operator; the app never
    # guesses an exchange rate.
    rate: Decimal = Field(..., gt=0, le=Decimal("100000000"))

    @field_validator("network", "asset")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _check(self):
        pattern = CRYPTO_NETWORKS.get(self.network)
        if pattern is None:
            raise ValueError(
                f"Network must be one of {', '.join(sorted(CRYPTO_NETWORKS))}"
            )
        if self.asset not in CRYPTO_ASSETS:
            raise ValueError(f"Asset must be one of {', '.join(sorted(CRYPTO_ASSETS))}")
        self.address = self.address.strip()
        if not pattern.fullmatch(self.address):
            raise ValueError(f"That is not a {self.network} address")
        return self


class CryptoSettings(BaseModel):
    enabled: bool = False
    wallets: list[CryptoWallet] = Field(default_factory=list, max_length=6)
    instructions: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _complete_when_enabled(self):
        if self.enabled and not self.wallets:
            raise ValueError("Enabled crypto payments need at least one wallet")
        keys = [(w.network, w.asset) for w in self.wallets]
        if len(keys) != len(set(keys)):
            raise ValueError("Each network and asset pair may appear only once")
        return self


class PaymentSettings(BaseModel):
    """What customers see on the top-up screen. Stored in ``app_settings``."""

    currency: str = "IRT"
    min_topup: Decimal = Field(default=Decimal("10000"), gt=0)
    max_topup: Decimal = Field(default=Decimal("50000000"), gt=0)
    card: CardSettings = Field(default_factory=CardSettings)
    crypto: CryptoSettings = Field(default_factory=CryptoSettings)

    @model_validator(mode="after")
    def _range(self):
        if self.min_topup > self.max_topup:
            raise ValueError("The minimum top-up is above the maximum")
        return self


# ------------------------------------------------------------ customer side
class CardMethodPublic(BaseModel):
    number: str
    holder: str
    bank: str
    instructions: str


class CryptoWalletPublic(BaseModel):
    network: str
    asset: str
    address: str
    rate: Decimal


class PaymentMethodsPublic(BaseModel):
    currency: str
    min_topup: Decimal
    max_topup: Decimal
    card: CardMethodPublic | None = None
    crypto: list[CryptoWalletPublic] = Field(default_factory=list)
    crypto_instructions: str = ""


class TopUpCreate(BaseModel):
    method: TopUpMethod
    amount: Decimal = Field(..., gt=0, max_digits=14, decimal_places=0)
    reference: str = Field(..., min_length=4, max_length=128)
    payer_note: str | None = Field(default=None, max_length=255)
    # Crypto only: which of the operator's wallets was paid.
    network: str | None = Field(default=None, max_length=16)
    asset: str | None = Field(default=None, max_length=16)
    # Pay this order from the wallet once the top-up is approved.
    order_id: str | None = Field(default=None, max_length=36)

    @field_validator("reference")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip().translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        return v

    @model_validator(mode="after")
    def _by_method(self):
        if self.method is TopUpMethod.CARD:
            if not _TRACKING.fullmatch(self.reference):
                raise ValueError("Enter the tracking number from your bank receipt")
        else:
            if not self.network:
                raise ValueError("Choose the network you paid on")
            if not _TX_HASH.fullmatch(self.reference):
                raise ValueError(
                    "Enter the transaction hash (TXID), not an address or link"
                )
        return self


class TopUpPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    method: TopUpMethod
    status: TopUpStatus
    amount: Decimal
    currency: str
    reference: str
    network: str | None = None
    asset: str | None = None
    crypto_amount: Decimal | None = None
    order_id: str | None = None
    credited_amount: Decimal | None = None
    reject_reason: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None


class WalletTransactionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: WalletTxKind
    amount: Decimal
    balance_after: Decimal
    currency: str
    order_id: str | None = None
    topup_id: str | None = None
    note: str | None = None
    created_at: datetime


class WalletPublic(BaseModel):
    balance: Decimal
    currency: str
    pending_topups: int
    transactions: list[WalletTransactionPublic]


# --------------------------------------------------------------- admin side
class TopUpApprove(BaseModel):
    # Credit a different amount when what arrived differs from the claim.
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=0)


class TopUpReject(BaseModel):
    reason: str = Field(..., min_length=2, max_length=255)


class WalletAdjust(BaseModel):
    # Signed: a positive amount credits, a negative one debits.
    amount: Decimal = Field(..., max_digits=14, decimal_places=0)
    note: str = Field(..., min_length=2, max_length=255)

    @field_validator("amount")
    @classmethod
    def _non_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("The amount cannot be zero")
        return v
