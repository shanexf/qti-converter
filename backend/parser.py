"""
Parses plain text (extracted from docx/pdf) into structured question objects.

Expected source convention (shown to the teacher in the frontend help panel):

    1. What is the capital of France?
    *a) Paris
    b) London
    c) Berlin
    d) Madrid

    2. The Great Wall of China is visible from space. (True/False)
    *False

    3. Match the following:
    A. Dog - 1. Bark
    B. Cat - 2. Meow

    4. The capital of Japan is ____.
    Answer: Tokyo

    5. Essay: Describe the causes of World War I.

Questions are separated by blank lines. A leading "N." numbers the question
(the number itself is discarded — QTI items get their own generated IDs).
"""
import re
import uuid

MC_OPTION_RE = re.compile(r"^(\*?)\s*([a-zA-Z])[\)\.]\s*(.+)$")
TF_BARE_RE = re.compile(r"^(\*?)\s*(True|False)\s*$", re.IGNORECASE)
MATCH_PAIR_RE = re.compile(
    r"^([A-Za-z0-9]+)[\.\)]\s*(.+?)\s*-\s*([A-Za-z0-9]+)[\.\)]\s*(.+)$"
)
ANSWER_RE = re.compile(r"^Answer:\s*(.+)$", re.IGNORECASE)
BLANK_RE = re.compile(r"_{3,}")


def _new_id():
    return uuid.uuid4().hex[:8]


def _strip_leading_number(line: str) -> str:
    return re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip()


def parse_questions(raw_text: str):
    # Normalize and split into blocks separated by one or more blank lines
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", text)
    questions = []

    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        first_line = _strip_leading_number(lines[0])
        rest = lines[1:]

        q = _parse_block(first_line, rest)
        if q:
            q["id"] = _new_id()
            questions.append(q)

    return questions


def _parse_block(first_line: str, rest: list):
    # Essay
    if first_line.lower().startswith("essay:"):
        prompt = first_line.split(":", 1)[1].strip()
        return {"type": "essay", "prompt": prompt}

    # Matching ("Match the following:" header, or pair lines directly present)
    pair_lines = [l for l in rest if MATCH_PAIR_RE.match(l)]
    if pair_lines and (
        "match" in first_line.lower() or len(pair_lines) == len(rest)
    ):
        pairs = []
        for l in pair_lines:
            m = MATCH_PAIR_RE.match(l)
            pairs.append(
                {
                    "left": m.group(2).strip(),
                    "right": m.group(4).strip(),
                }
            )
        prompt = first_line if "match" in first_line.lower() else "Match the following:"
        return {"type": "matching", "prompt": prompt, "pairs": pairs}

    # True/False shorthand: bare "*True" / "False" lines, no letter prefix
    tf_lines = [l for l in rest if TF_BARE_RE.match(l)]
    if tf_lines and len(tf_lines) == len(rest):
        if len(tf_lines) == 1:
            # Shorthand: just states the correct answer, e.g. a lone "*True"
            m = TF_BARE_RE.match(tf_lines[0])
            answer_value = m.group(2).capitalize()
            choices = [{"text": "True"}, {"text": "False"}]
            correct_index = 0 if answer_value == "True" else 1
        else:
            choices = []
            correct_index = None
            for i, l in enumerate(tf_lines):
                m = TF_BARE_RE.match(l)
                if m.group(1):
                    correct_index = i
                choices.append({"text": m.group(2).capitalize()})
            if correct_index is None:
                correct_index = 0
        return {
            "type": "truefalse",
            "prompt": first_line,
            "choices": choices,
            "correct_index": correct_index,
        }

    # Multiple choice / True-False (lettered options, "*" marks correct)
    option_lines = [l for l in rest if MC_OPTION_RE.match(l)]
    if option_lines:
        choices = []
        correct_index = None
        for i, l in enumerate(option_lines):
            m = MC_OPTION_RE.match(l)
            is_correct = bool(m.group(1))
            text = m.group(3).strip()
            if is_correct:
                correct_index = i
            choices.append({"text": text})
        is_tf = len(choices) == 2 and {
            c["text"].strip().lower() for c in choices
        } <= {"true", "false"}
        return {
            "type": "truefalse" if is_tf else "multiple_choice",
            "prompt": first_line,
            "choices": choices,
            "correct_index": correct_index if correct_index is not None else 0,
        }

    # Fill in the blank (has "Answer:" line and/or underscores in the prompt)
    answer_line = next((l for l in rest if ANSWER_RE.match(l)), None)
    if answer_line or BLANK_RE.search(first_line):
        m = ANSWER_RE.match(answer_line) if answer_line else None
        answer = m.group(1).strip() if m else ""
        return {"type": "fill_blank", "prompt": first_line, "answer": answer}

    # Fallback: treat as short-answer/essay so nothing silently disappears
    prompt = " ".join([first_line] + rest)
    return {"type": "essay", "prompt": prompt}
