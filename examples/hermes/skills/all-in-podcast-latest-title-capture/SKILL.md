---
name: all-in-podcast-latest-title-capture
version: 1.0
description: Captures the title of the top YouTube video from the 'All-in Podcast' search results and saves it to a sanitized file in the user's Downloads folder.
---
# All-in Podcast Latest Title Capture
## Purpose
This skill automates capturing the title of the top YouTube video from the 'All-in Podcast' search results on YouTube, extracting the title, sanitizing it, and saving the raw title text into a uniquely named file within the user's Downloads directory (`~/Downloads/`).

## Execution Flow
This process must be executed as a sequence of tool calls, best wrapped in an `execute_code` block for reliable state management.

**Step 1: Navigate to YouTube**
Action: `browser_navigate`
Command: `browser_navigate(url='https://www.youtube.com/')`

**Step 2: Search for Content**
Action: `browser_type` (Type 'All-in podcast' into the search bar, using the reference ID obtained from the snapshot)
Action: `browser_click` (Click the Search button, using the reference ID obtained from the snapshot)

**Step 3: Extract the Latest Title (JS Execution)**
Action: `browser_console`
Code/Expression: `document.querySelector('h3 a[href*="/watch?v="]:nth-of-type(1)').innerText`
*Note: This JS selector targets the primary video result's title link.*

**Step 4: Sanitize and Save the Title (Python/Execute_Code)**
This step must be executed via `execute_code` using Python logic for reliability.

**Python Code Template (For use in execute_code):**
```python
import re
from hermes_tools import write_file

# IMPORTANT: Before running this block, execute Step 3 and capture the result 
# into a variable named 'raw_title' within the execution context or pass it as an argument.

raw_title = "[[PASTE_EXTRACTED_TITLE_HERE]]" 

if not raw_title:
    print("ERROR: Could not extract video title. Check browser selectors (Step 3).")
else:
    # 1. Clean up common separators (commas, multiple spaces)
    sanitized_title = re.sub(r'[,\s]+', ' ', raw_title).strip()
    
    # 2. Create safe filename: Remove characters that are illegal or problematic in filenames, replace spaces with underscores.
    safe_filename = re.sub(r'[^\w\s\-]', '', sanitized_title).replace(' ', '_')
    
    # 3. Final check and file writing
    if not safe_filename:
        print("ERROR: Sanitization resulted in an empty filename.")
    else:
        # Target path is hardcoded based on user context/memory
        file_path = f"~/Downloads/{safe_filename}.txt"
        content_to_save = raw_title
        
        # Writing the file
        write_file(path=file_path, content=content_to_save)
        
        print(f"SUCCESS: 影片標題已存檔到 {file_path}")
        print("---")
        print(f"原始標題內容: {content_to_save}")
        print("---")
        print("檔案名稱採用標題內容，並進行了無效字元清理，儲存於您的 Downloads 資料夾。")
```

## Usage Notes
- **Fragility:** This skill is highly dependent on YouTube's DOM structure. If the page layout changes, Step 3 selector must be updated.
- **Execution Order:** Must run steps sequentially: Navigate -> Search -> Extract (console) -> Save (script).