# GoDark skill

This file is for an AI agent (or a person) that needs to trade or build on GoDark. Read it before you invent an integration. Prefer the official docs and SDKs over guessing endpoints.

Human docs: [Introduction](/docs/overview) · [Programmatic Access](/docs/programmatic-access) · [SDKs](/docs/sdks)

---

## What GoDark is

GoDark is a **dark pool** for **perpetual futures**.

A dark pool means the order book is **not a public tape**. You do not get a live view of everyone else's bids and offers. The public market feed is **aggregates only** — things like volume, open interest, and funding — not the book.

Matching happens **off-chain** in a matching engine. The book is a **CLOB** (central limit order book) with **price–time priority**: better price first; at the same price, earlier arrival wins.

Orders are **HPKE-encrypted in transit**. That protects the message on the way in. The book itself is a normal price–time CLOB, not an encrypted data structure.

**Collateral** lives in a program-controlled **collateral vault** on **Solana**. Trading balances sit on GoDark's ledger. **Fills do not move SPL** — a trade does not send tokens on every match. Solana records **checkpoints**, not a public per-user fill ledger.

Do not describe GoDark as an MPC committee, a shielded pool, or a CEX lookalike with a public order book.

---

## What a person must do first

An agent cannot skip signup. Someone has to do this in the app:

1. **Sign up** with email. Identity is wallet-based.
2. **Connect a wallet.** That can be a [Dynamic](https://www.dynamic.xyz/) embedded wallet (Fireblocks underneath) or an external wallet such as Phantom, Solflare, or MetaMask. Dynamic is wallet-as-a-service: it provisions the embedded wallet and also lets the user fund from those external wallets.
3. **See funds in the funding wallet.** Incoming USDC shows there first.
4. **Deposit into the trading wallet.** That move puts USDC into **shared custody** in the **collateral vault**. Only then is it available to trade.
5. **Create API credentials** in the app (Settings → API Key Management). You need a key id, secret, and passphrase.

On **testnet**, use the [GoDark Testnet Faucet](https://faucet.godark-dex.com/) to get funds.

More detail: [Connect Wallet](/docs/connect-wallet) · [Deposit Funds](/docs/deposit-funds)

### Two wallets (do not mix them up)

| Name | What it is |
| --- | --- |
| **Funding wallet** | The wallet connected at signup (Dynamic or external). USDC arrives here. |
| **Trading wallet** | Where you deposit for trading. That deposit moves USDC into the collateral vault. |

Withdrawals release USDC from the collateral vault toward a recipient address. Ghost withdrawals can use a fresh ephemeral address so the payout is not publicly linked to the funding wallet.

---

## How you should integrate

**Use an official SDK.** That is the primary path. The SDKs handle login, session setup, and order encryption. Do not hand-roll a trading WebSocket client unless you have a specific reason.

Clone the examples repo for the language you want. Each repo **vendors** the SDK — you do not need a private package registry. Copy `.env.example` to `.env`, set credentials, and run the samples.

| Language | Docs | Clone |
| --- | --- | --- |
| Python | [Python SDK](/docs/sdks/python) | `git clone https://github.com/gq-godark/gdx-python-sdk-examples.git` |
| Rust | [Rust SDK](/docs/sdks/rust) | `git clone https://github.com/gq-godark/gdx-rust-sdk-examples.git` |
| JavaScript / TypeScript | [JavaScript SDK](/docs/sdks/javascript) | `git clone https://github.com/gq-godark/gdx-js-sdk-examples.git` |
| C++ | [C++ SDK](/docs/sdks/cpp) | `git clone https://github.com/gq-godark/gdx-cpp-sdk-examples.git` |
| Go | [Go SDK](/docs/sdks/go) | `git clone https://github.com/gq-godark/gdx-go-sdk-examples.git` |
| Java | [Java SDK](/docs/sdks/java) | `git clone https://github.com/gq-godark/gdx-java-sdk-examples.git` |

Environment variables (OS env wins over `.env`):

| Variable | Required | Meaning |
| --- | --- | --- |
| `GODARK_API_KEY_ID` | Yes | API key id from the app |
| `GODARK_API_SECRET` | Yes | API secret |
| `GODARK_PASSPHRASE` | Yes | Passphrase for that key |
| `GODARK_EDGE_URL` | No | Override the edge / WebSocket origin if you are not on the default testnet |

Never commit secrets. Never put keys in chat logs if you can avoid it.

### REST and SSE (secondary)

Use these only when you are **not** going through an SDK.

| Interface | Protocol | Use for |
| --- | --- | --- |
| REST | HTTPS | Auth and account APIs |
| SSE | HTTPS | Live stats and notifications |

**Trading** — place, cancel, modify, and live order/position streams — is **WebSocket**. The SDKs own that path.

---

## How to behave as an agent

1. Read this file, then the linked docs if you need more than this summary.
2. Confirm the human has an app account, a funded **trading wallet**, and API credentials.
3. Clone the matching examples repo and use the vendored SDK. Follow that repo's README.
4. Check balance / positions before placing orders.
5. Prefer a dry-run or a small size if the SDK supports a preview.
6. Do not scrape a public order book — there isn't one.
7. Do not tell the user their fills settle as an SPL transfer per trade. They do not.
8. Do not invent MCP servers, a `godark` CLI, or MPC/committee flows. Those are not the integration path.

---

## More docs

| Page | When to open it |
| --- | --- |
| [Introduction](/docs/overview) | Venue model in one table |
| [Connect Wallet](/docs/connect-wallet) | Signup and identity |
| [Deposit Funds](/docs/deposit-funds) | Funding wallet → trading wallet → vault |
| [Trading Interface](/docs/trading-interface) | How the web app is laid out |
| [Programmatic Access](/docs/programmatic-access) | SDK-first integration |
| [SDKs](/docs/sdks) | All languages, env vars, client types |
