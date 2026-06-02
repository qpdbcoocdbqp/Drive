---
name: read-text-document
description: "Extracts raw text content from PDF and DOCX documents. Supports text-based PDFs via pymupdf and DOCX files via python-docx."
version: 1.3.0
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

Ensure the required libraries are installed in the execution environment:

```bash
uv pip install pymupdf python-docx
```

## Goal

To extract complete, structured plain text content from a given document file path (PDF or DOCX).

## Usage Instructions

### 1. Recommended Method: Using `uv run` Tool

To ensure the script runs in the same environment where dependencies are installed (especially when using tools like `uv`), use the `uv run` command via the `terminal` tool:

```bash
uv run python /opt/data/skills/productivity/read-text-document/scripts/read_document.py --file /path/to/your/document.pdf
# or
uv run python /opt/data/skills/productivity/read-text-document/scripts/read_document.py --file /path/to/your/document.docx
```

This method ensures the script runs within the intended, dependency-aware environment.

## Limitations & Best Practices

- **Scanned PDFs**: Text-based extraction will not work on image-based (scanned) PDFs. Use the `ocr-and-documents` skill in such cases.
- **Environment Pitfall**: Always ensure dependencies are installed in the *exact* Python environment used to run the CLI script. If installation is performed outside the running agent session's environment, a `ModuleNotFoundError` may still occur.
- **Complex Formatting**: Tables, headers, footers, and complex layouts may require additional post-processing.
- **Large Files**: Very large documents may consume significant memory during extraction.
- **DOCX Support**: Only `.docx` files are supported. Legacy `.doc` files are not supported by `python-docx`.
