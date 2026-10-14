# SkillCertify

**AI-Verified, Plagiarism-Checked Skill Credential Registry on GenLayer**

A credential registry where anyone can submit proof of a claimed skill, have
an on-chain LLM judge its tier and genuinely demonstrate it, and be
semantically checked against every previously-certified proof so nobody can
get credentialed twice on a reworded copy of someone else's already-approved
work.

---

## What it does

1. Anyone can **request certification** for a claimed skill by submitting a
   proof URL (code, writing, a project — anything that demonstrates the
   skill) and paying a certification fee set by the contract owner at
   deployment.
2. The fee is **automatically routed to the owner**, and any overpayment is
   **automatically refunded** to the requester in the same transaction.
3. Anyone can trigger **evaluation**: the contract fetches the proof content
   and asks an LLM to produce, in one reconciled response, both a skill tier
   (`EXPERT` / `INTERMEDIATE` / `BEGINNER` / `NOT_QUALIFIED`) and a concise
   summary of what the proof actually demonstrates. GenLayer's validator
   consensus (`gl.eq_principle.prompt_comparative`) ensures multiple
   independent LLM runs agree on both the categorical tier and the substance
   of the summary before anything is accepted on-chain.
4. If the tier is `NOT_QUALIFIED`, the request is rejected outright — no
   credential is issued.
5. Otherwise, the validator-agreed summary is **embedded** and checked via
   `VecDB` against every proof that has ever been successfully `CERTIFIED`.
   If it's too semantically similar to an existing certified proof, the
   request is rejected as a duplicate — the credential can't be farmed by
   resubmitting someone else's (or your own) already-certified work.
6. Otherwise, the proof is **certified**, joins the registry, and becomes
   searchable by anyone via semantic similarity.
7. The contract **owner can revoke** a certification later if fraud is
   discovered.

## Why this is different

Every other contract in this series moves funds strictly between the two
transacting parties (a poster and a worker, a staker and a pool). SkillCertify
introduces a third role — the **protocol owner**, captured once at deploy
time, who automatically earns a fee on every request, with automatic refund
of any overpayment. It's also the first contract to chain an LLM judgment
directly into a VecDB similarity check within the same write call: the
judge produces both the categorical verdict *and* the exact text that gets
embedded, all inside the same `eq_principle`-reconciled closure, so every
validator embeds the identical validator-agreed string rather than raw,
non-deterministic web content. And it's the first contract where an
owner/admin role can unilaterally invalidate a credential a user already
holds.

## Architecture

| Concern | Mechanism |
|---|---|
| Credential state | Parallel `TreeMap[str, ...]` fields per cert ID (requester, skill name, proof URL, status, tier, summary, reasoning, duplicate-of) |
| Protocol fee | `fee_wei: u256` set once at deploy time; transferred to `owner` on every `request_certification` call; any excess payment refunded to the requester automatically |
| AI evaluation | `gl.nondet.web.render` fetches the proof URL; `gl.nondet.exec_prompt` inside a closure, reconciled across validators via `gl.eq_principle.prompt_comparative`, producing both the tier and the summary in one agreed response |
| Output safety | Evaluator output is validated (JSON parse, structure, field presence, type, and domain of the `tier` field; summary must be a non-empty string) before any state mutation |
| Duplicate detection | The validator-agreed `summary` is embedded (`all-MiniLM-L6-v2`, 384-dim) and checked via `VecDB.knn` against `proof_registry`, which only ever contains proofs that reached `CERTIFIED` status; a nearest-neighbor distance under `DUPLICATE_DISTANCE_THRESHOLD = 0.15` triggers rejection |
| Governance | `revoke_certification` is restricted to `gl.message.sender_address == self.owner`, and can only act on a currently `CERTIFIED` entry |

## Contract methods

### Write

- **`request_certification(skill_name: str, proof_url: str) -> str`** *(payable)*
  Pays the certification fee (overpayment refunded automatically). Returns
  the new `cert_id` (e.g. `"C0"`).
- **`evaluate_certification(cert_id: str) -> str`**
  Triggers AI evaluation and, if qualified and not a duplicate, certifies
  the proof. Returns one of `"CERTIFIED - <tier> - <reasoning>"`,
  `"REJECTED_DUPLICATE - too similar to already-certified proof <id>"`, or
  `"NOT_QUALIFIED - <reasoning>"`.
- **`revoke_certification(cert_id: str)`**
  Owner-only. Flips a `CERTIFIED` entry to `REVOKED`.

### View

- **`get_certification(cert_id: str) -> dict`**
  Full current state of a certification (requester, skill name, proof URL,
  status, tier, summary, reasoning, duplicate-of).
- **`find_similar_proofs(text: str, top_n: int) -> list[dict]`**
  Semantic search over the certified-proof registry — returns the closest
  matches to any text, with each match's cert ID, skill name, summary, and
  distance.
- **`get_cert_count() -> int`**
- **`get_fee() -> str`**
- **`get_owner() -> str`**

## Tested end-to-end in GenLayer Studio

- Deployed with `fee_wei: 1000000000000000000` (1 GEN).
- **Bad proof rejected correctly:** `request_certification` with official
  project documentation (not evidence of personal skill) as the proof URL
  was evaluated as **`NOT_QUALIFIED`**, with reasoning explicitly noting it
  was "verbatim official documentation with zero evidence" of personal
  work — no credential issued, confirming the contract doesn't certify
  proof that doesn't actually demonstrate the claimant's own skill.
- **Genuine proof certified correctly:** a real, authored code
  implementation (a quicksort algorithm) was evaluated as
  **`CERTIFIED - INTERMEDIATE`**, with the LLM's reasoning citing specific
  qualities of the implementation. `get_certification` confirmed
  `status: "CERTIFIED"` with a populated `tier` and `summary`.
- **Duplicate correctly rejected:** resubmitting the same proof URL under a
  new request was evaluated as **`"REJECTED_DUPLICATE - too similar to
  already-certified proof C1"`**, with full validator consensus (`Accepted`
  through `PENDING → PROPOSING → COMMITTING → REVEALING → ACCEPTED`,
  multiple validator policies agreeing).
- **Revocation confirmed:** `revoke_certification` on the certified entry
  succeeded, and a follow-up `get_certification` confirmed
  `status: "REVOKED"`.
- All test transactions reached `FINALIZED`/`SUCCESS`.

## Deployment

```
# v0.2.16
# { "Seq": [
#     { "Depends": "py-lib-genlayer-embeddings:09h0i209wrzh4xzq86f79c60x0ifs7xcjwl53ysrnw06i54ddxyi" },
#     { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
# ] }
```

Requires the `genlayer-embeddings` dependency for `VecDB` and
`SentenceTransformer`. Constructor takes one argument, `fee_wei: int` — the
certification fee in wei, set once at deploy time. Deploy directly in
GenLayer Studio or via `genlayer-js` against Studionet / Bradbury testnet.

## Notes on GenVM quirks discovered while building this

- Producing the categorical verdict and the text to be embedded in the
  *same* `eq_principle`-reconciled LLM call (rather than embedding raw
  fetched web content separately) keeps the embedding step safely
  deterministic — every validator embeds the identical, already-agreed
  summary string rather than re-fetching and potentially getting
  non-identical web content.
- `VecDB`'s `.knn(embedding, n)` returns objects exposing `.distance` and
  `.value` (the stored dataclass) — checking `matches[0].distance <
  THRESHOLD` before trusting a match avoids false positives when the
  registry is empty or the nearest neighbor isn't actually close.
- The duplicate registry is only ever populated with `CERTIFIED` proofs
  (never `PENDING`, `NOT_QUALIFIED`, or `REJECTED_DUPLICATE` ones) — this
  keeps the duplicate check meaningful (comparing only against entries that
  actually earned a credential) and keeps the VecDB from growing on
  requests that never became real credentials.

## Notes on design decisions

- The evaluator's output is validated (JSON structure, field presence,
  type, and domain of the `tier` field; non-empty summary) before any state
  change — the same safety pattern applied proactively across every
  contract in this series after it was flagged as a required fix on an
  earlier contract (SemanticDisputeRouter).
- The certification fee is non-refundable once evaluation runs (even if the
  result is `NOT_QUALIFIED` or `REJECTED_DUPLICATE`) — this mirrors how
  real-world certification and application fees typically work, and keeps
  the contract from needing to track a refundable-vs-spent fee state.
- `revoke_certification` only changes status, not history — the original
  tier, summary, and reasoning stay on-chain for transparency, so a revoked
  credential's record remains auditable rather than erased.

## License

MIT
