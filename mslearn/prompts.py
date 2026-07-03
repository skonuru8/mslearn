from mslearn.opsdb import OpsDB

PROMPTS: dict[str, str] = {
    "extraction": (
        "You extract factual claims from one text chunk of a learning source.\n"
        "Return JSON only, matching the given schema.\n"
        "Rules:\n"
        "- Each claim is one self-contained factual or prescriptive statement.\n"
        "- 'quote' MUST be a verbatim substring copied character-for-character from the chunk"
        " that supports the claim. Never paraphrase inside 'quote'.\n"
        "- 'stance' is 'recommends' if the source advises doing it, 'warns_against' if it advises"
        " against it, else 'neutral'.\n"
        "- Extract at most 8 claims. Skip greetings, filler, and table-of-contents text.\n"
        "- If the chunk contains no claims, return {\"claims\": []}.\n"
    ),
    "extraction_retry_suffix": (
        "\nYour previous attempt failed validation: {reasons}.\n"
        "Copy 'quote' EXACTLY from the chunk text — character for character."
    ),
    "concept_match": (
        "You decide whether candidate claims express the SAME underlying concept "
        "or practice as an anchor claim.\n"
        "Input includes one anchor claim id/text and numbered candidate claims.\n"
        "Return JSON only matching schema: {\"matches\": [\"<claim_id>\", ...]}.\n"
        "Rules:\n"
        "- Only include claim_id values from provided candidates.\n"
        "- Include ids only when meaning matches concept-level equivalence, not just topic overlap.\n"
        "- If none match, return {\"matches\": []}.\n"
    ),
    "conflict_scan": (
        "You classify intra-concept conflicts for a set of claims with ids and stances.\n"
        "{domain_guidance}\n"
        "Return JSON only matching schema:\n"
        "{\"conflicts\": [{\"claim_a\": \"...\", \"claim_b\": \"...\", "
        "\"classification\": \"context_dependent|outdated|genuine_debate|evidence_mismatch\", "
        "\"rationale\": \"...\"}]}\n"
        "Rules:\n"
        "- Use only provided claim ids.\n"
        "- Classification must be exactly one taxonomy value.\n"
        "- If no conflicts, return {\"conflicts\": []}.\n"
    ),
    "concept_name": (
        "You name and summarize one concept from its claims.\n"
        "Return JSON only matching schema: "
        "{\"name\": \"<3-6 words>\", \"summary\": \"<2 sentences>\"}.\n"
        "Rules:\n"
        "- Name is concise, instructional, and source-grounded.\n"
        "- Summary captures what learner should retain across claims.\n"
    ),
    "concept_deps": (
        "You infer prerequisite edges between numbered concepts (ids + names).\n"
        "Return JSON only matching schema:\n"
        "{\"edges\": [{\"from_concept\": \"...\", \"to_concept\": \"...\"}]}\n"
        "Semantics: from_concept DEPENDS_ON to_concept (to must be learned first).\n"
        "Rules:\n"
        "- Use only provided concept ids.\n"
        "- Include only prerequisite edges.\n"
        "- Avoid cycles; if unsure, return fewer edges.\n"
    ),
}


def get_prompt(db: OpsDB, name: str) -> str:
    if name not in PROMPTS:
        raise KeyError(f"unknown prompt {name!r}")
    override = db.get_setting(f"prompt:{name}")
    return override if override is not None else PROMPTS[name]


def domain_guidance(profile: str) -> str:
    if profile == "technical":
        return (
            "Domain guidance: prefer context_dependent when claims both hold under "
            "different operating conditions."
        )
    if profile == "interpretive":
        return (
            "Domain guidance: prefer genuine_debate and preserve competing framings "
            "when sources disagree in interpretation."
        )
    raise KeyError(f"unknown domain profile {profile!r}")


def get_domain_profile(db: OpsDB) -> str:
    return db.get_setting("corpus.domain_profile", "technical")
