import json

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5"

SEARCH_TOOL = {
    "name": "search",
    "description": (
        "Trigger a new Soulseek search for the given query. Use this when "
        "the user wants to search for a new song, artist, or album - not "
        "when they are referring to existing results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query text, e.g. artist and track name.",
            }
        },
        "required": ["query"],
    },
}

DOWNLOAD_TOOL = {
    "name": "download",
    "description": (
        "Queue a download from the most recent search results. Use 'index' "
        "when the user refers to a result by its position (e.g. 'the "
        "second one', 'number 3'). Use 'match' when the user refers to a "
        "result by a distinguishing word or phrase from its filename (e.g. "
        "'the one that sounds like Blue Monday', 'the flac version'). "
        "Provide exactly one of index or match, never both."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "description": "1-based index into the most recent search results.",
            },
            "match": {
                "type": "string",
                "description": "A substring to match (case-insensitive) against result filenames.",
            },
        },
    },
}

LIST_RESULTS_TOOL = {
    "name": "list_results",
    "description": (
        "Replay the most recent search results without triggering a new "
        "search. Use this when the user asks to hear the results again or "
        "asks what the options were."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

TOOLS = [SEARCH_TOOL, DOWNLOAD_TOOL, LIST_RESULTS_TOOL]

SYSTEM_PROMPT = (
    "You are the command-resolution layer for a voice-controlled Soulseek "
    "(Nicotine+) client. The user just spoke or typed a command; your job "
    "is to resolve it to exactly one of the three provided tools. You will "
    "always be given the most recent search results (if any) as JSON "
    "context. Always call exactly one tool - never respond with plain text."
)


def translate_tool_call(name: str, tool_input: dict) -> dict:
    if name == "search":
        return {"action": "search", "query": tool_input["query"]}

    if name == "download":
        if "index" in tool_input:
            return {"action": "download", "index": tool_input["index"]}

        if "match" in tool_input:
            return {"action": "download", "match": tool_input["match"]}

        raise ValueError("download tool call missing both 'index' and 'match'")

    if name == "list_results":
        return {"action": "list_results"}

    raise ValueError(f"unknown tool name: {name!r}")


def _format_last_results(last_results: list) -> str:
    if not last_results:
        return "No search results yet."

    return json.dumps(last_results, indent=2)


def resolve_transcript(transcript: str, last_results: list, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    message_content = (
        f"Most recent search results:\n{_format_last_results(last_results)}\n\n"
        f"User command: {transcript}"
    )

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": message_content}],
    )

    for block in response.content:
        if block.type == "tool_use":
            return translate_tool_call(block.name, block.input)

    raise RuntimeError("Claude did not return a tool call despite tool_choice='any'")
