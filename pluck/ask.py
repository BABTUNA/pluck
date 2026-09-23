"""The chooser: one cheap call answers every question with a single letter,
and token logprobs give a real probability per answer.

Output format is one letter per line in question order, so each answer is one
token and its top_logprobs entry is a distribution over the options.
"""

import math
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
CHOOSER_MODEL = os.environ.get("PLUCK_CHOOSER", "openai/gpt-4.1-nano")
LETTERS = "ABCDEFGHIJKLM"


class Question:
    def __init__(self, key: str, prompt: str, options: list[str], allow_none: bool = True):
        self.key = key
        self.prompt = prompt
        self.options = options
        self.allow_none = allow_none

    def render(self, i: int) -> str:
        opts = "\n".join(f"  {LETTERS[j]}) {o}" for j, o in enumerate(self.options))
        none = f"\n  N) none of these" if self.allow_none else ""
        return f"Q{i}. {self.prompt}\n{opts}{none}"


class Answer:
    def __init__(self, letter: str, probability: float, distribution: dict[str, float]):
        self.letter = letter
        self.probability = probability
        self.distribution = distribution

    def pick(self, options: list[str]):
        i = LETTERS.find(self.letter)
        return options[i] if 0 <= i < len(options) else None


async def ask(state: str, questions: list[Question]) -> tuple[dict[str, Answer], dict]:
    """One call, all questions. Returns answers keyed by question key + usage."""
    qs = "\n\n".join(q.render(i + 1) for i, q in enumerate(questions))
    messages = [
        {"role": "system", "content":
         "You answer multiple-choice questions about a product page. "
         "Reply with EXACTLY one line per question. Each line is ONE capital letter and "
         "nothing else: no numbering, no punctuation, no words. "
         "Line 1 answers Q1, line 2 answers Q2, and so on."},
        {"role": "user", "content": f"{state}\n\n{qs}"},
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            OPENROUTER,
            headers={"Authorization": f"Bearer {os.environ['OPEN_ROUTER_API_KEY']}"},
            json={
                "model": CHOOSER_MODEL,
                "messages": messages,
                "max_tokens": 20 + 4 * len(questions),
                "logprobs": True,
                "top_logprobs": 12,
            },
        )
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"] or ""
    tokens = (data["choices"][0].get("logprobs") or {}).get("content") or []

    # content lines are the source of truth for WHICH letter answers each
    # question; logprob tokens supply probabilities only when they align
    lines = []
    for l in content.splitlines():
        m = re.fullmatch(r"[^A-Za-z]*([A-Z])[^A-Za-z]*", l.strip())
        if m:
            lines.append(m.group(1))
    letter_toks = [t for t in tokens if re.fullmatch(r"[A-Z]", t.get("token", "").strip())]
    aligned = len(letter_toks) == len(lines)

    answers: dict[str, Answer] = {}
    for i, q in enumerate(questions):
        if i >= len(lines):
            break
        letter = lines[i]
        prob, dist = 0.5, {}
        if aligned:
            for alt in letter_toks[i].get("top_logprobs", []):
                a = alt.get("token", "").strip()
                if re.fullmatch(r"[A-Z]", a):
                    dist[a] = dist.get(a, 0.0) + math.exp(alt.get("logprob", -99))
            total = sum(dist.values()) or 1.0
            dist = {k: v / total for k, v in dist.items()}
            prob = dist.get(letter, 0.0)
        answers[q.key] = Answer(letter, prob, dist)

    usage = data.get("usage", {})
    return answers, usage
