from hermes_tools import web_extract, json_parse
import re

def extract_latest_allin_episode(url="https://allin.com/episodes"):
    """
    Fetches the episodes archive and extracts the latest episode title and number
    by targeting the structured list in the content.
    """
    print(f"Fetching content from: {url}")
    try:
        # Use web_extract to get the content (this is the token-heavy part we can't eliminate entirely)
        result = web_extract(urls=[url])
        if not result.get("results") or not result["results"][0].get("content"):
            return {"error": "Failed to retrieve content from URL."}

        content = result["results"][0]["content"]
        
        # Regex to find the most recent episode. We look for a pattern like "Episode #XXX"
        # Since the archive is chronological, we look for the entry at the top (most recent).
        # Pattern: looks for a title/guest list followed by a date/episode number.
        # We look for a strong episode marker that precedes the main content.
        episode_pattern = re.compile(r"(### 📅 Episode #\d+|\d{4}/\d{1,2}/\d{1,2}).*?(\*\*.*\*\*|\d{4}/\d{1,2}/\d{1,2}).*$", re.DOTALL | re.MULTILINE)
        
        # Using finditer to get all matches and assume the first one is the latest due to chronological order.
        matches = list(episode_pattern.finditer(content))
        
        if not matches:
            return {"error": "Could not find any episode entries matching the expected format."}

        # The first match found by regex is assumed to be the latest due to document order.
        latest_match = matches[0]
        
        # Further parsing to isolate title/number from the matched block
        full_match_text = latest_match.group(0)
        
        # Find episode number (e.g., #276)
        number_match = re.search(r"(Episode #\d+)", full_match_text)
        episode_num = number_match.group(0) if number_match else "Unknown"

        # Find the title (usually the first bold/main text after the header)
        title_match = re.search(r"(.*)\n", full_match_text)
        title = title_match.group(1).strip() if title_match else full_match_text[:100]
        
        return {
            "success": True,
            "episode_number": episode_num,
            "episode_title": title,
            "source_text_preview": full_match_text[:500] + "..."
        }

    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    # Execute the extraction function
    result = extract_latest_allin_episode()
    print(result)