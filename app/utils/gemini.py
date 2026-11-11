import os

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def generate_json(model, prompt, max_output_tokens, *, system_instruction=None, schema=None):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is required")

    thinking = types.ThinkingConfig(thinking_level="MINIMAL") if model == DEFAULT_MODEL else None
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=schema,
        system_instruction=system_instruction,
        max_output_tokens=max_output_tokens,
        thinking_config=thinking,
    )
    # The worker accounts for each attempt before calling Gemini.
    options = types.HttpOptions(timeout=25_000, retry_options=types.HttpRetryOptions(attempts=1))
    with genai.Client(api_key=key, vertexai=False, http_options=options) as client:
        return client.models.generate_content(model=model, contents=prompt, config=config)
