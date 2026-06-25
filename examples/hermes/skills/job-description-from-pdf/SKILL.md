---
name: job-description-from-pdf
description: "Extract company, job name, job description (JD), and other conditions from a PDF and output the result in a structured JSON file."
version: 1.2.0
author: Qwythos
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Document, PDF, JSON, Extraction, Job Posting]
    related_skills: [read-text-document]
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

## Robustness
 - The two-step workflow (raw text extraction + custom parsing) ensures stable, correct parsing on the first attempt.
 Raw text extraction captures all content; custom parsing applies targeted heuristics for company names, job titles, descriptions, and conditions.
