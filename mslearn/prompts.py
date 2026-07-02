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
}


def get_prompt(db: OpsDB, name: str) -> str:
    if name not in PROMPTS:
        raise KeyError(f"unknown prompt {name!r}")
    override = db.get_setting(f"prompt:{name}")
    return override if override is not None else PROMPTS[name]
