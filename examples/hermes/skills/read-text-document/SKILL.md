---
name: read-text-document
description: "Extracts raw text content from a text-based PDF file using pymupdf. Best for non-scanned documents."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Text-Extraction, pymupdf]
    related_skills: [ocr-and-documents]
---

# 📄 Read Text Document (PDF)

This skill uses `pymupdf` to reliably extract all visible text from a standard, text-based PDF file.

## 🛠️ Prerequisites
This skill assumes `pymupdf` is installed in the environment:
```bash
pip install pymupdf
```

## 🎯 Goal
To retrieve the complete, structured text content from a given PDF file path.

## ⚙️ Workflow (Recommended Method)
Execute the following Python code using the `execute_code` tool:

```python
from hermes_tools import execute_code
import pymupdf

pdf_path = "path/to/your/document.pdf" # <<< REPLACE THIS WITH YOUR FILE PATH

try:
    doc = pymupdf.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "n"
    doc.close()
    print(full_text)
except Exception as e:
    print(f"Error processing PDF with pymupdf: {e}")
```

## ⚠️ Pitfalls & When to Use Alternatives
1.  **Scanned PDFs/Images**: If the PDF is an image scan (i.e., text is not selectable), `pymupdf` will extract empty strings or gibberish. **Use the `ocr-and-documents` skill** for these cases, as it utilizes OCR.
2.  **Complex Layouts**: For highly complex documents (tables, forms, heavy graphical elements), the output might require post-processing.
3.  **Speed**: This is very fast for text-based PDFs.

## 🔄 Advanced Usage (Inline Python)
For quick, single-file operations without setting up a full script, you can use the inline Python execution via the tool call:
```python
import pymupdf
doc = pymupdf.open("path/to/your/document.pdf")
for page in doc:
    print(page.get_text())
doc.close()
```
