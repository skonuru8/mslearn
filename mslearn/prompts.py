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
        "Input includes one anchor claim id/text and a numbered list of candidate "
        "claims; each candidate line shows its list number followed by its real "
        "claim_id, e.g. '3. <claim_id> | <text> | stance=...'.\n"
        "Return JSON only matching schema: {\"matches\": [\"<claim_id>\", ...]}.\n"
        "Rules:\n"
        "- Each entry in 'matches' MUST be the candidate's claim_id string "
        "exactly as shown, never the list number that precedes it.\n"
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
    "concept_categories": (
        "You group numbered study concepts into a few coherent categories.\n"
        "Return JSON only: {\"categories\": [{\"name\": \"<2-4 words>\","
        " \"concept_ids\": [\"...\"]}]}\n"
        "Rules:\n"
        "- Use only the provided concept ids; assign each to exactly one category.\n"
        "- Aim for 2-8 categories; group by subject, not by order.\n"
        "- Category names are short and human-readable.\n"
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
    "rubric_guide": (
        "Score a study-guide JSON for one concept.\n"
        "Concept: {concept_name}\nSummary: {concept_summary}\nGuide JSON:\n{guide}\n"
        "Return JSON: {{\"depth_1_5\": n, \"redundancy_1_5\": n,"
        " \"category_fit_1_5\": n, \"grounding_1_5\": n}}.\n"
        "depth: sections explain (what/why/how/example) vs restate in one line.\n"
        "redundancy: HIGH score = little repetition of the summary or between sections.\n"
        "category_fit: concept fits a coherent category.\n"
        "grounding: every item ties to a real claim.\n"
    ),
    "provenance_check": (
        "Given markdown and source claims, detect unsupported factual statements.\n"
        "Markdown:\n{markdown}\n"
        "Claims:\n{claims}\n"
        "Return JSON: unsupported_fact (bool), offending_sentence (string)."
    ),
    "guide": (
        "You write a study guide from a concept's already-extracted claims.\n"
        "{domain_guidance}\n"
        "Concept: {concept_name}\nSummary: {concept_summary}\n"
        "Claims (each is a grounded fact; cite by id, do not copy the wording):\n{claims}\n"
        "Memory hints:\n{memory_hints}\n"
        "Return JSON only matching the schema. Rules:\n"
        "- For each claim, write a section item that EXPLAINS it in your own words:"
        " what it means, why it holds, how it connects to the concept and to the"
        " other claims, and -- where a claim supports one -- a concrete example."
        " Several sentences, not a one-line restatement, and never a near-paraphrase"
        " of the source wording. Set 'text' to your explanation, 'kind' to the"
        " claim's kind, and 'claims' to the id(s) it rests on.\n"
        "- Every supplied claim id must be covered by exactly one grounded item."
        " Never invent items, text, or claim ids. Go deep on what the claims"
        " support; never pad with generic filler or facts beyond them.\n"
        "- Group items into 2-6 sections; 'skeleton' lists section titles in order."
        " Give each section a short id (s1,s2,...).\n"
        "- 'tl_dr.text' is one plain orienting sentence citing the 1-2 claim ids it"
        " rests on in tl_dr.claims.\n"
        "- Memory hints personalize ordering only; never a source of facts.\n"
    ),
    "flashcards": (
        "You turn a concept's already-extracted claims into flashcards for"
        " spaced-repetition study.\n"
        "{domain_guidance}\n"
        "Concept: {concept_name}\nSummary: {concept_summary}\n"
        "Claims (each is a verbatim-grounded fact you must NOT reword):\n{claims}\n"
        "Memory hints:\n{memory_hints}\n"
        "Return JSON only matching the schema. Rules:\n"
        "- Each card's 'front' is a short question/prompt; 'back' is the answer.\n"
        "- Every card must cite the claim id(s) it is grounded in via 'claims';"
        " never invent a card with no claim id.\n"
        "- Prefer one card per claim; skip claims that don't make a good"
        " quiz-style question rather than inventing filler.\n"
        "- Memory hints personalize phrasing only; never a source of facts.\n"
    ),
    "selfcheck": (
        "You write self-check questions from a concept's already-extracted"
        " claims so a learner can test their own understanding.\n"
        "{domain_guidance}\n"
        "Concept: {concept_name}\nSummary: {concept_summary}\n"
        "Claims (each is a verbatim-grounded fact you must NOT reword):\n{claims}\n"
        "Memory hints:\n{memory_hints}\n"
        "Return JSON only matching the schema. Rules:\n"
        "- Each check's 'question' probes understanding or application, not"
        " just recall.\n"
        "- 'answer' is the model answer, grounded only in the supplied claims.\n"
        "- Every check must cite the claim id(s) it rests on via 'claims';"
        " never invent a check with no claim id.\n"
        "- Memory hints personalize phrasing only; never a source of facts.\n"
    ),
    "evolve_propose": (
        "Propose up to 3 tunable or prompt changes to improve eval metrics.\n"
        "Current metrics:\n{metrics}\n"
        "Current tunables:\n{tunables}\n"
        "Recent audit:\n{audit}\n"
        "Patterns:\n{patterns}\n"
        "Return JSON proposals with kind, key, value/new_prompt, targets_metric, why."
    ),
    "patterns_summarize": (
        "You cluster recurring problems from recent user feedback and rejected"
        " self-improvement proposals into a small set of named failure patterns.\n"
        "Recent negative feedback:\n{feedback}\n"
        "Recently rejected evolution proposals:\n{rejected_history}\n"
        "Return JSON only matching schema: {{\"patterns\": [{{\"name\": \"...\","
        " \"symptom\": \"...\", \"evidence\": \"...\","
        " \"suggested_target_metric\": \"...\"}}]}}.\n"
        "Rules:\n"
        "- Group related feedback/rejections into one pattern instead of listing"
        " each individually.\n"
        "- 'symptom' describes what the user or judge is seeing, in plain words.\n"
        "- 'evidence' cites the concept ids, tags, or rejected proposal keys that"
        " support the pattern.\n"
        "- 'suggested_target_metric' is a real metric key (e.g. guide.depth,"
        " feedback.wrong_rate) the pattern points at.\n"
        "- If there is no clear recurring pattern, return {{\"patterns\": []}}.\n"
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
