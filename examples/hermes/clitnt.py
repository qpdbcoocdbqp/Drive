import os
from openai import OpenAI


client = OpenAI(
    api_key="http://localhost:8642/v1",
    base_url="your-secret-key"
)

def chat_with_hermes(message, history=None, model="hermes-agent", stream=False):
    messages = history if history else []
    messages.append({"role": "user", "content": message})
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=stream
        )
        
        if stream:
            full_content = ""
            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                print(content, end="", flush=True)
                full_content += content
            return full_content
        else:
            return response.choices[0].message.content
            
    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    print("=== Test Chat Completions ===")
    res1 = chat_with_hermes("請解釋什麼是量子計算")
    print(f"\nResponse 1: {res1}\n")

    print("=== Test History ===")
    test_history = [
        {"role": "system", "content": "你是一個 Python 專家"},
        {"role": "user", "content": "寫一個遞迴的費波那契函數"}
    ]
    res2 = chat_with_hermes("請給我代碼", history=test_history)
    print(f"Response 2: {res2}")
    
    test_history.append({"role": "assistant", "content": res2})
    res3 = chat_with_hermes("請為這段代碼加上 Type Hints", history=test_history)
    print(f"\nResponse 3 (Type Hints): {res3}\n")

    print("=== Test Response Object Attributes ===")
    raw_response = client.chat.completions.create(
        model="hermes-agent",
        messages=[{"role": "user", "content": "Hi"}]
    )
    print(f"Model Used: {raw_response.model}")
    print(f"Usage: {raw_response.usage}")
