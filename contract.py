# v0.2.16
# { "Seq": [
#     { "Depends": "py-lib-genlayer-embeddings:09h0i209wrzh4xzq86f79c60x0ifs7xcjwl53ysrnw06i54ddxyi" },
#     { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
# ] }
import json
import typing
import numpy as np
from dataclasses import dataclass
from genlayer import *
import genlayer_embeddings as gle

DUPLICATE_DISTANCE_THRESHOLD = 0.15

@gl.evm.contract_interface
class _Recipient:
    class View:
        pass
    class Write:
        pass

@allow_storage
@dataclass
class CertifiedProof:
    cert_id: str
    skill_name: str
    summary: str

class SkillCertify(gl.Contract):
    owner: Address
    fee_wei: u256
    cert_counter: u256

    requester_map: TreeMap[str, Address]
    skill_name_map: TreeMap[str, str]
    proof_url_map: TreeMap[str, str]
    status_map: TreeMap[str, str]
    tier_map: TreeMap[str, str]
    summary_map: TreeMap[str, str]
    reasoning_map: TreeMap[str, str]
    duplicate_of_map: TreeMap[str, str]

    proof_registry: gle.VecDB[np.float32, typing.Literal[384], CertifiedProof]

    def __init__(self, fee_wei: int):
        self.owner = gl.message.sender_address
        self.fee_wei = u256(fee_wei)
        self.cert_counter = u256(0)

    def get_embedding_generator(self):
        return gle.SentenceTransformer("all-MiniLM-L6-v2")

    def get_embedding(self, text: str) -> np.ndarray[tuple[typing.Literal[384]], np.dtypes.Float32DType]:
        return self.get_embedding_generator()(text)

    @gl.public.write.payable
    def request_certification(self, skill_name: str, proof_url: str) -> str:
        assert gl.message.value >= self.fee_wei, "Payment does not cover the certification fee"

        cert_id = f"C{self.cert_counter}"
        self.cert_counter += u256(1)

        self.requester_map[cert_id] = gl.message.sender_address
        self.skill_name_map[cert_id] = skill_name
        self.proof_url_map[cert_id] = proof_url
        self.status_map[cert_id] = "PENDING"
        self.tier_map[cert_id] = ""
        self.summary_map[cert_id] = ""
        self.reasoning_map[cert_id] = ""
        self.duplicate_of_map[cert_id] = ""

        if self.fee_wei > u256(0):
            _Recipient(self.owner).emit_transfer(value=self.fee_wei)

        excess = gl.message.value - self.fee_wei
        if excess > u256(0):
            _Recipient(gl.message.sender_address).emit_transfer(value=excess)

        return cert_id

    @gl.public.write
    def evaluate_certification(self, cert_id: str) -> str:
        assert self.status_map[cert_id] == "PENDING", "Certification not pending"

        skill_name = self.skill_name_map[cert_id]
        proof_url = self.proof_url_map[cert_id]

        def judge() -> str:
            content = gl.nondet.web.render(proof_url, mode="text")
            prompt = f"""You are assessing proof of a claimed skill for a certification registry.

Claimed skill: \"\"\"{skill_name}\"\"\"

Proof content (fetched from the submitted URL):
\"\"\"{content[:3000]}\"\"\"

Assess this proof in two parts:
1. A skill tier, chosen strictly from this set: "EXPERT", "INTERMEDIATE",
   "BEGINNER", or "NOT_QUALIFIED" (use NOT_QUALIFIED if the content does not
   genuinely demonstrate the claimed skill at all).
2. A concise one-to-two sentence summary of what the proof actually
   demonstrates, specific enough that two near-identical proofs would get
   near-identical summaries, but written in your own words.

Respond with strict JSON only, no markdown, no extra text:
{{"tier": "EXPERT", "summary": "<1-2 sentences>", "reasoning": "<one sentence>"}}
"""
            return gl.nondet.exec_prompt(prompt)

        raw = gl.eq_principle.prompt_comparative(
            judge,
            "The tier field must match exactly (EXPERT, INTERMEDIATE, BEGINNER, or NOT_QUALIFIED). The summary must describe the same specific content and skill demonstrated, though wording may vary. Reasoning wording may vary but must express the same underlying judgment.",
        )

        try:
            parsed = json.loads(raw)
        except Exception:
            raise Exception("Evaluator output was not valid JSON; refusing to evaluate.")

        assert isinstance(parsed, dict), "Evaluator output was not a JSON object; refusing to evaluate."
        assert "tier" in parsed, "Evaluator output missing 'tier' field; refusing to evaluate."
        tier = parsed["tier"]
        assert isinstance(tier, str), "Evaluator 'tier' field was not a string; refusing to evaluate."
        assert tier in ("EXPERT", "INTERMEDIATE", "BEGINNER", "NOT_QUALIFIED"), "Evaluator 'tier' was outside the allowed domain; refusing to evaluate."

        summary = parsed.get("summary", "")
        assert isinstance(summary, str) and len(summary) > 0, "Evaluator 'summary' field was missing or not a string; refusing to evaluate."

        reasoning = parsed.get("reasoning", "")
        assert isinstance(reasoning, str), "Evaluator 'reasoning' field was not a string; refusing to evaluate."

        self.tier_map[cert_id] = tier
        self.summary_map[cert_id] = summary
        self.reasoning_map[cert_id] = reasoning

        if tier == "NOT_QUALIFIED":
            self.status_map[cert_id] = "NOT_QUALIFIED"
            return f"NOT_QUALIFIED - {reasoning}"

        embedding = self.get_embedding(summary)
        matches = list(self.proof_registry.knn(embedding, 1))
        if len(matches) > 0 and matches[0].distance < DUPLICATE_DISTANCE_THRESHOLD:
            self.status_map[cert_id] = "REJECTED_DUPLICATE"
            self.duplicate_of_map[cert_id] = matches[0].value.cert_id
            return f"REJECTED_DUPLICATE - too similar to already-certified proof {matches[0].value.cert_id}"

        self.status_map[cert_id] = "CERTIFIED"
        self.proof_registry.insert(embedding, CertifiedProof(cert_id=cert_id, skill_name=skill_name, summary=summary))
        return f"CERTIFIED - {tier} - {reasoning}"

    @gl.public.write
    def revoke_certification(self, cert_id: str) -> None:
        assert gl.message.sender_address == self.owner, "Only the registry owner can revoke a certification"
        assert self.status_map[cert_id] == "CERTIFIED", "Only a currently-certified entry can be revoked"
        self.status_map[cert_id] = "REVOKED"

    @gl.public.view
    def get_certification(self, cert_id: str) -> dict:
        return {
            "cert_id": cert_id,
            "requester": str(self.requester_map[cert_id]),
            "skill_name": self.skill_name_map[cert_id],
            "proof_url": self.proof_url_map[cert_id],
            "status": self.status_map[cert_id],
            "tier": self.tier_map[cert_id],
            "summary": self.summary_map[cert_id],
            "reasoning": self.reasoning_map[cert_id],
            "duplicate_of": self.duplicate_of_map[cert_id],
        }

    @gl.public.view
    def find_similar_proofs(self, text: str, top_n: int) -> list[dict]:
        embedding = self.get_embedding(text)
        results = []
        for match in self.proof_registry.knn(embedding, top_n):
            results.append({
                "cert_id": match.value.cert_id,
                "skill_name": match.value.skill_name,
                "summary": match.value.summary,
                "distance": str(match.distance),
            })
        return results

    @gl.public.view
    def get_cert_count(self) -> int:
        return int(self.cert_counter)

    @gl.public.view
    def get_fee(self) -> str:
        return str(self.fee_wei)

    @gl.public.view
    def get_owner(self) -> str:
        return str(self.owner)
