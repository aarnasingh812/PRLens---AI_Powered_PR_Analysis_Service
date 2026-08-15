from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv('GROQ_API_KEY')


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
```json
"""

    client = Groq(api_key=key)
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.6,
        top_p=1
    )

    # BUG FIX: was `message.conten` (missing 't')
    return completion.choices[0].message.content
