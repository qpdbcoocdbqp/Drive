---
name: job-description-from-pdf
description: "Extract company, job name, job description (JD), and other conditions from a PDF and output the result in a structured JSON file."
version: 1.0.0
author: Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Document, PDF, JSON, Extraction, Job Posting]
---

# Job Description from PDF

This skill automates the process of reading a PDF document and extracting structured information relevant to a job posting.

## Goal
To extract the following structured data from a PDF:
- `主要公司` (Main Company)
- `職缺名稱` (Job Name)
- `工作內容` (Job Description/Responsibilities)
- `其他條件` (Other Conditions, e.g., experience, education)
The final output is a JSON file named `/output/<job_name>_<date>.json`.

## Prerequisites
- The `productivity/read-text-document` skill must be installed and available.
- The execution environment must have the necessary Python dependencies (`pymupdf`, `python-docx`) if running the extraction logic locally.

## Workflow (Chaining Skills)

This task is best executed by chaining the raw text extraction with a custom parsing step.

### Step 1: Extract Raw Text (Using `read-text-document`)
Use the `productivity/read-text-document` skill to convert the PDF file into plain, raw markdown text.

**Command Example:**
```bash
uv run python /opt/data/skills/productivity/read-text-document/scripts/read_document.py --file /path/to/document.pdf
```

### Step 2: Parse and Structure Data (Using `execute_code`)
Pass the raw text output from Step 1 into a Python script (via `execute_code`) that performs the necessary extraction, cleaning, and formatting into the required JSON schema.

**Required JSON Schema:**
```json
{
  "主要公司": "string",
  "職缺名稱": "string",
  "工作內容": ["string", "string", ...],
  "其他條件": {
    "工作經歷": "string",
    "學歷要求": "string",
    "語文條件": "string",
    "其他技能要求": ["string", "string", ...]
  }
}
```

**Execution Logic (Internal to `execute_code`):**
1.  Receive the raw text content as input.
2.  Use string manipulation and regex (or a specialized LLM call if complex) to identify the company name, job title, JD list, and condition fields.
3.  Generate a timestamp for the filename.
4.  Write the final JSON object to `/output/<job_name>_<date>.json` using the `write_file` tool.

## Limitations & Best Practices
- **Text Quality**: The extraction quality heavily depends on the PDF being text-based. If the PDF is scanned, use the `ocr-and-documents` skill.
- **Parsing Complexity**: Complex or poorly formatted layouts (e.g., tables, mixed languages) may require iterative refinement of the extraction logic in Step 2.
- **Output**: The final deliverable is the successfully created JSON file.
