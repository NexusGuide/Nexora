# Payments and the wallet

Customers pay into a **wallet**, and orders are paid **from** the wallet.
There is no payment gateway: money is sent outside the app, by card-to-card
transfer or in crypto, and an admin checks it before anything is credited.

```
customer sends money ──► files a top-up (tracking no. / TXID) ──► admin checks it
                                                                   │
                         wallet credited ◄── approve ──────────────┘
                              │
            order paid from wallet ──► service created on the panel
```

If the customer started from "Buy", the top-up carries that order, and
approving the top-up also pays the order and queues its provisioning. The
customer does not have to come back and tap again.

## Setting it up

1. Deploy (`bash scripts/update.sh`). The migration adds the wallet tables and
   gives every existing account a balance of 0.
2. Open `/admin` → **تنظیمات پرداخت** (Payment settings). Only the **owner**
   can see or change this page: whoever can edit it can redirect every
   customer's money to their own card.
3. Enter what customers should see:
   - **Card**: the 16-digit card number (checked for a valid Luhn checksum),
     the holder's name and the bank.
   - **Crypto**: one or more wallets, each with its network (TRC20, BEP20,
     ERC20, TON), asset (USDT, TRX, TON, USDC), address, and **your rate** in
     Toman per unit. The address is checked against the network's format.
     The app never guesses an exchange rate; keep it current yourself.
   - The minimum and maximum top-up.
4. Save. The change, including the full new settings, is written to the audit
   log.

None of this is in the repository or in the APK. It lives in the database and
reaches the app only through `GET /api/v1/payment-methods`, which requires a
signed-in customer.

## Reviewing payments

`/admin` → **پرداخت‌ها** (Payments) lists pending top-ups, oldest first.

- **Card**: find the transfer in your bank statement by the tracking number
  and the amount. The customer may also give the last four digits of their
  card.
- **Crypto**: look the TXID up on the chain's explorer and check the
  destination address, the amount and that it is confirmed. The row shows the
  exact crypto amount and the rate the customer was quoted.
- A red badge marks a reference that appears on more than one request: the same
  receipt filed twice, possibly from two accounts.

**Approve** asks for the amount that actually arrived, prefilled with the
claim. Correct it if the customer typed the wrong figure. **Reject** asks for a
reason, which the customer sees.

Roles: owner, manager and finance can review payments and correct wallets.
Support cannot touch money.

## Guarantees

| Rule | Enforced by |
|---|---|
| A balance never goes below zero | `CHECK (wallet_balance >= 0)` on `users` |
| A top-up is credited once, however often Approve is pressed | UNIQUE `idempotency_key` on `wallet_transactions` (`topup:<id>`) |
| An order is charged once, however often Pay is pressed | the same key (`order:<id>`), plus the order row lock |
| Balance and history agree | both written in one transaction with the user row locked; nothing else writes the balance |
| The same receipt is not credited twice | a pending or approved top-up with the same method and reference blocks a new one |
| A customer cannot flood the queue | at most 3 pending top-ups per account |

The concurrency rules were exercised against PostgreSQL 16: two orders paid at
once from a balance that covers only one pays exactly one, and one order paid
three times at once is charged once.

## Manual corrections

A customer's page in `/admin` shows their balance and wallet history, with
**اصلاح دستی موجودی** (manual adjustment): a signed amount and a reason. Use it
for refunds and goodwill credits. It cannot overdraw the wallet, and every
adjustment is in the audit log with who made it.

## API

Customer (signed in):

| Method | Path | |
|---|---|---|
| GET | `/api/v1/payment-methods` | card and crypto details, min and max |
| GET | `/api/v1/wallet` | balance, pending count, last 30 transactions |
| GET | `/api/v1/wallet/topups` | the customer's top-up requests |
| POST | `/api/v1/wallet/topups` | file a payment for review |
| POST | `/api/v1/wallet/topups/{id}/cancel` | withdraw a pending one |
| POST | `/api/v1/orders/{id}/pay` | pay an order from the wallet (idempotent) |

Admin:

| Method | Path | Capability |
|---|---|---|
| GET, PUT | `/api/v1/admin/payment-settings` | `payments.settings` (owner) |
| GET | `/api/v1/admin/topups?status=` | `payments` |
| POST | `/api/v1/admin/topups/{id}/approve` | `payments` |
| POST | `/api/v1/admin/topups/{id}/reject` | `payments` |
| GET | `/api/v1/admin/users/{id}/wallet` | `payments` |
| POST | `/api/v1/admin/users/{id}/wallet/adjust` | `payments` |

`POST /admin/orders/{id}/confirm-payment` still exists: it marks an order
paid without touching any wallet, for a payment settled some other way.

## Not included

- **No payment gateway.** Zarinpal and similar need a merchant account; the
  `app/payments` package is where an adapter would go.
- **No receipt images.** The tracking number or TXID is what gets checked;
  storing photos of bank receipts would mean storing customers' card and
  account details.
- **No automatic chain checking.** Approving crypto is manual.
- **No refunds to card or wallet address.** A refund is a manual adjustment
  plus sending the money back yourself.
