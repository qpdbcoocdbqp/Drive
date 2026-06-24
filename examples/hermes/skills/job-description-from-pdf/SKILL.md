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
  "campany": "string",
  "vacancy": "string",
  "job_description": ["string", "string", ...],
  "other_conditions": ["string", "string", ...],
}
```
  This script utilizes the internal LLM parsing logic to handle layout variations and automatically outputs the final JSON.

  ### Alternative Workflow (Step-by-Step)
  If a standalone script is not preferred, the manual workflow is:
  1.  Use the `productivity/read-text-document` skill to convert the PDF file into plain, raw markdown text.
  2.  Pass the raw text output from Step 1 into a Python script (via `execute_code`) that performs the necessary extraction, cleaning, and formatting into the required JSON schema.

  Required JSON Schema:
  ...

- **Parsing Complexity**: Complex or poorly formatted layouts (e.g., tables, mixed languages) may require iterative refinement of the extraction logic in Step 2.
- **Output**: The final deliverable is the successfully created JSON file.
