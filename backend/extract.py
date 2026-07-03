"""
Extracts raw text from an uploaded .docx or .pdf file.
"""
import io
import docx
import pdfplumber


def extract_text(filename: str, file_bytes: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".docx"):
        return _extract_docx(file_bytes)
    elif lower.endswith(".pdf"):
        return _extract_pdf(file_bytes)
    else:
        raise ValueError("Unsupported file type. Please upload a .docx or .pdf file.")


def _extract_docx(file_bytes: bytes) -> str:
    document = docx.Document(io.BytesIO(file_bytes))
    lines = [p.text for p in document.paragraphs]
    return "\n".join(lines)


def _extract_pdf(file_bytes: bytes) -> str:
    lines = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.append(text)
    return "\n".join(lines)
