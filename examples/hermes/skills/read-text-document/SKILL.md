---
name: read-text-document
description: "Extracts raw text content from PDF and DOCX documents. Supports text-based PDFs via pymupdf and DOCX files via python-docx."
version: 1.2.0
author: Grok
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Document, PDF, DOCX, Text-Extraction]
    related_skills: [ocr-and-documents]
---

# Read Text Document

This skill provides reliable text extraction capabilities for both PDF and Microsoft Word (DOCX) documents.

## Supported Formats
- **PDF**: Text-based (non-scanned) documents using `pymupdf`
- **DOCX**: Microsoft Word documents using `python-docx`

## Prerequisites

Ensure the required libraries are installed in the environment:

```bash
pip install pymupdf python-docx
```

## Goal

To extract complete, structured plain text content from a given document file path (PDF or DOCX).

## Usage Instructions

### 1. Recommended Method: Using `execute_code` Tool

Execute the following Python code using the `execute_code` tool:

```python
from hermes_tools import execute_code

def extract_text(file_path: str) -> str:
    """Extract text from PDF or DOCX file, including tables."""
    import os
    
    if not os.path.exists(file_path):
        return f"Error: File not found - {file_path}"
    
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == ".pdf":
            import pymupdf
            doc = pymupdf.open(file_path)
            full_text = ""
            for page in doc:
                full_text += page.get_text("text") + "\n"
            doc.close()
            return full_text.strip()
            
        elif ext in [".docx", ".doc"]:
            from docx import Document
            doc = Document(file_path)
            
            full_text_list = []
            
            # Extract text from paragraphs
            for paragraph in doc.paragraphs:
                full_text_list.append(paragraph.text)
                
            # Extract text from tables
            for table in doc.tables:
                for row in table.rows:
                    row_text = []
                    for cell in row.cells:
                        cell_text = []
                        for paragraph in cell.paragraphs:
                            cell_text.append(paragraph.text)
                        row_text.append(" ".join(cell_text))
                    full_text_list.append("\n".join(row_text))
            
            return "\n".join(full_text_list).strip()
            
        else:
            return f"Error: Unsupported file format - {ext}"
            
    except Exception as e:
        return f"Error processing document: {str(e)}"

# Replace with your actual file path
file_path = "/path/to/your/document.pdf"   # or .docx
result = extract_text(file_path)
print(result)
```

### 2. Advanced Usage (Inline Execution)

For quick extraction, you may use inline Python code:

**For PDF:**
```python
import pymupdf
doc = pymupdf.open("path/to/document.pdf")
full_text = "\n".join([page.get_text("text") for page in doc])
doc.close()
print(full_text)
```

**For DOCX:**
```python
from docx import Document
doc = Document("path/to/document.docx")
full_text_list = []
# Extract text from paragraphs
for paragraph in doc.paragraphs:
    full_text_list.append(paragraph.text)
# Extract text from tables
for table in doc.tables:
    for row in table.rows:
        row_text = []
        for cell in row.cells:
            cell_text = []
            for paragraph in cell.paragraphs:
                cell_text.append(paragraph.text)
            row_text.append(" ".join(cell_text))
    full_text_list.append("\n".join(row_text))
print("\n".join(full_text_list))
```

## Limitations & Best Practices

- **Scanned PDFs**: Text-based extraction will not work on image-based (scanned) PDFs. Use the `ocr-and-documents` skill in such cases.
- **Complex Formatting**: Tables, headers, footers, and complex layouts may require additional post-processing.
- **Large Files**: Very large documents may consume significant memory during extraction.
- **DOCX Support**: Only `.docx` files are supported. Legacy `.doc` files are not supported by `python-docx`.
