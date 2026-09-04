import json
from openai_codex import Codex

def main():
    print("Inspect Codex SDK JSON-RPC rounting map")
    client = Codex()
    
    try:
        # 1. get skill list
        skills_response = client._client._request_raw("skills/list", {})
        print("\n--- Codex Skills 清單 (skills/list) ---")
        print(json.dumps(skills_response, indent=2, ensure_ascii=False))

        # 2. get plugin list
        plugins_response = client._client._request_raw("plugin/list", {})
        print("\n--- Codex Plugins 清單 (plugin/list) ---")
        print(json.dumps(plugins_response, indent=2, ensure_ascii=False))

        # 3. get models list
        models_response = client._client._request_raw("model/list", {})
        print("\n--- Codex Models 清單 (model/list) ---")
        print(json.dumps(models_response, indent=2, ensure_ascii=False))

    finally:
        # close client
        client.close()

if __name__ == "__main__":
    main()
