import os
from dotenv import load_dotenv

from .gemini import DEFAULT_MODEL, generate_json

load_dotenv()


def analyze_code_with_llm(file_content: str, file_name: str) -> str:
    prompt = f"""
Analyse the following code for:
- Code style and formatting issues
- Potential bugs or errors
- Performance improvements
- Best practices

File: {file_name}
Content:
{file_content}

Provide a detailed JSON output with the structure:
{{
    "issues": [
        {{
            "type": "<style|bug|performance|best_practice>",
            "line": <line_number>,
            "description": "<description>",
            "suggestion": "<suggestion>"
        }}
    ]
}}
"""

    model = os.getenv("PRLENS_REVIEW_MODEL", DEFAULT_MODEL)
    response = generate_json(model, prompt, max_output_tokens=4096)
    if not response.text:
        raise ValueError("Gemini returned no review content")
    return response.text
