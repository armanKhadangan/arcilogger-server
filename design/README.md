# Design contracts / Tasarım sözleşmeleri

Each larger change (a "wave") is specified first in a design contract here, reviewed until no
blocker is left, and then built in work packages by the team. The contract is binding: setting
keys, defaults and bounds, error codes, JSON shapes, texts, status rows and the tests it lists are
fixed there, so that packages built in parallel fit together.

Her büyük değişiklik (bir "dalga") önce burada bir tasarım sözleşmesiyle tanımlanır, engelleyici
kalmayana kadar incelenir, sonra ekip tarafından iş paketleri hâlinde yapılır. Sözleşme
bağlayıcıdır.

| Wave | Contract | Integration branch | Work packages |
|---|---|---|---|
| 3 — FortiGate-only DHCP evidence, srcmac, per-exit NAT (dual WAN) | [wave-3-contract.md](wave-3-contract.md) | `wave-3` | §4 of the contract; one GitHub issue each |
| 4 — Uyum Durumu, Sistem, first-run wizard | [wave-4-contract.md](wave-4-contract.md) | `wave-4` | §4 of the contract; one GitHub issue each |

How to read a contract (the GitHub issue of your package says which sections you need):

- **§0** decisions and why; **§1** the facts the design rests on; **§2** the implementation
  contract (what to build, exactly); **§3** the tests; **§4** the work packages, file ownership and
  merge order;
- **Implementation notes** (one section per review round): smaller points each package checks
  against the code it writes and handles, or explains in its pull request why not;
- **Deviations**: what a package had to do differently, with the reason. Append yours there in the
  same pull request.
