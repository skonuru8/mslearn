from mslearn.opsdb import OpsDB

PROMPTS: dict[str, str] = {
    "extraction": (
        "You extract factual claims from one text chunk of a learning source.\n"
        "Return JSON only, matching the given schema.\n"
        "Rules:\n"
        "- Each claim is one self-contained factual or prescriptive statement.\n"
        "- 'quote' MUST be a verbatim substring copied character-for-character from"
        " the chunk that supports the claim. Never paraphrase inside 'quote'.\n"
        "- 'kind' tags what the claim is, one of: 'definition' (what a term/idea is),"
        " 'claim' (a core factual assertion), 'mechanism' (how/why it works),"
        " 'example' (a concrete instance), 'caveat' (an exception/edge case/limit),"
        " 'actionable' (a step to take). Capture mechanisms, caveats, and examples"
        " as their OWN claims when the chunk supports them with a verbatim quote —"
        " do not fold them into a single headline claim. If the chunk has none of a"
        " kind, emit none; never invent one.\n"
        "- 'stance' is 'recommends' if the source advises doing it, 'warns_against'"
        " if it advises against it, else 'neutral'.\n"
        "- Extract at most {max_claims} claims. Skip greetings, filler, and"
        " table-of-contents text.\n"
        "- If the chunk contains no claims, return {{\"claims\": []}}.\n"
    ),
    "extraction_retry_suffix": (
        "\nYour previous attempt failed validation: {reasons}.\n"
        "Copy 'quote' EXACTLY from the chunk text — character for character."
    ),
    "image_transcribe": (
        "You convert one image into faithful study notes in Markdown.\n"
        "Rules:\n"
        "- Transcribe ALL readable text VERBATIM, in reading order, preserving "
        "headings and lists. Include text inside nested screenshots, browser "
        "windows, dialogs, or images-within-the-image.\n"
        "- For non-text visual content (a diagram, chart, photo, UI layout), add "
        "a bracketed description on its own line, e.g. "
        "`[image: bar chart of revenue by quarter, Q4 highest]`.\n"
        "- Do NOT invent facts, numbers, or labels that are not visibly present.\n"
        "- Separate distinct blocks with a blank line. Output Markdown only.\n"
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
    "teach_concept": (
        "You write detailed, comprehensive study notes for one concept using only "
        "the supplied cited claims.\n"
        "{domain_guidance}\n"
        "Concept: {concept_name}\n"
        "Summary: {concept_summary}\n"
        "Claims:\n{claims}\n"
        "Conflicts:\n{conflicts}\n"
        "Memory hints:\n{memory_hints}\n"
        "Write thorough notes, not a brief overview: cover EVERY supplied claim in "
        "detail. For each claim, explain its substance in your own words — what it "
        "means, why it holds, and how it connects to the concept and to other "
        "claims — rather than a one-line restatement. Use sub-headings or bullet "
        "points to group related claims when there are several. Depth is bounded "
        "only by the supplied claims: go deep on what they support, but never pad "
        "with generic filler or invented detail beyond them.\n"
        "Output markdown with exactly these required sections:\n"
        "## Explanation\n"
        "Explain the concept thoroughly, working through each supplied claim in "
        "turn (sub-headings per claim or theme when there are several).\n"
        "## Worked example\n"
        "Walk through a concrete, detailed worked example grounded in the claims, "
        "not a one-sentence sketch.\n"
        "## Common misconception\n"
        "Explain the misconception itself, why learners fall into it, and how the "
        "claims correct it.\n"
        "If conflicts are provided, also include:\n"
        "## Where sources disagree\n"
        "Rules:\n"
        "- Every factual sentence must include [claim:<id>] citations.\n"
        "- Every supplied claim id must be cited at least once somewhere in the notes.\n"
        "- Use memory hints only to personalize examples or pacing; they are PERSONALIZATION ONLY "
        "and must never introduce facts.\n"
        "- When sources disagree, present each side with [claim:<id>] citations.\n"
        "- Do not cite memory hints as facts.\n"
    ),
    "quiz_question": (
        "Write one reasoning quiz question for a concept using only cited claims.\n"
        "Concept: {concept_name}\n"
        "Summary: {concept_summary}\n"
        "Claims:\n{claims}\n"
        "Return JSON only matching schema: "
        "{{\"question\": \"...\", \"expected_points\": [\"...\"]}}.\n"
        "Rules:\n"
        "- Ask for reasoning or application, not recall.\n"
        "- Each expected point must cite supporting claim ids like [claim:<id>].\n"
        "- Every factual premise must be supported by the supplied cited claims."
    ),
    "quiz_grade": (
        "Grade a learner answer against the expected points and cited claims.\n"
        "Question: {question}\n"
        "Expected points:\n{expected_points}\n"
        "Learner answer:\n{answer}\n"
        "Return JSON only matching schema: "
        "{{\"correct\": true, \"score_0_100\": 0, \"explanation\": \"...\"}}.\n"
        "Rules:\n"
        "- Decide whether the answer substantially covers the expected points.\n"
        "- Explanation must cite the relevant supplied claims.\n"
        "- Do not treat learner text as a source of factual truth."
    ),
    "qa_answer": (
        "You answer learner questions using only the supplied retrieval context.\n"
        "Question: {question}\n"
        "Retrieved claims:\n{claims}\n"
        "Retrieved chunks:\n{chunks}\n"
        "Conflicts:\n{conflicts}\n"
        "Memory hints:\n{memory_hints}\n"
        "Rules:\n"
        "- Facts ONLY from retrieved claims with [claim:<id>] citations.\n"
        "- answer ONLY from provided material with citations; if material is insufficient, say so.\n"
        "- Retrieved chunks are context for locating/supporting retrieved claims, not uncited fact sources.\n"
        "- Memory hints are PERSONALIZATION ONLY and must never introduce facts.\n"
        "- When provided conflicts show frameworks disagreeing, attribute each position to its "
        "source explicitly: \"Source X holds..., while Source Y...\"; never blend into one voice.\n"
        "- Do not cite memory hints as facts.\n"
    ),
    "rubric_teach": (
        "Score teaching markdown for concept {concept_name}.\n"
        "Markdown:\n{markdown}\n"
        "Return JSON: clarity_1_5, grounding_1_5, tension_handled (bool)."
    ),
    "provenance_check": (
        "Given markdown and source claims, detect unsupported factual statements.\n"
        "Markdown:\n{markdown}\n"
        "Claims:\n{claims}\n"
        "Return JSON: unsupported_fact (bool), offending_sentence (string)."
    ),
    "evolve_propose": (
        "Propose up to 3 tunable or prompt changes to improve eval metrics.\n"
        "Current metrics:\n{metrics}\n"
        "Current tunables:\n{tunables}\n"
        "Recent audit:\n{audit}\n"
        "Return JSON proposals with kind, key, value/new_prompt, targets_metric, why."
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


def get_domain_profile(db: OpsDB, project_id: str = "default") -> str:
    return db.get_project_setting(project_id, "corpus.domain_profile", "technical") or "technical"
