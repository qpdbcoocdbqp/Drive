import os
import openai


client = openai.Client(
    base_url="http://localhost:8642/v1",
    api_key="your-secret-key"
)

print(client.models.list())

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

def response_with_hermes(message, instructions=None, model="hermes-agent", previous_response_id=None, store=True):
    try:
        resp = client.responses.create(
            model=model,
            instructions=instructions,
            input=message,
            previous_response_id=previous_response_id,
            store=store
        )
        for item in resp.output:
            if item.type == "function_call":
                print(f"🛠️ function_call: {item.name}({item.arguments})")
            elif item.type == "function_call_output":
                print(f"📋 function_call_output: {item.output}")
            elif item.type == "message":
                text = item.content[0].text if hasattr(item.content[0], 'text') else item.content[0]['text']
                print(f"🤖 assistant: {text}")
        print("\n--- continue (previous_response_id) ---")
        return resp
    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    print("=== Test Chat Completions ===")
    res1 = chat_with_hermes("Explain what quantum computing is.")
    print(f"\nResponse 1: {res1}\n")

    print("=== Test History ===")
    test_history = [
        {"role": "system", "content": "You are a Python expert"},
        {"role": "user", "content": "Write a recursive Fibonacci function"}
    ]
    res2 = chat_with_hermes("Please provide the code", history=test_history)
    print(f"Response 2: {res2}")
    
    test_history.append({"role": "assistant", "content": res2})
    res3 = chat_with_hermes("Please add Type Hints to this code", history=test_history)
    print(f"\nResponse 3 (Type Hints): {res3}\n")

    print("=== Test Response Object Attributes ===")
    raw_response = client.chat.completions.create(
        model="hermes-agent",
        messages=[{"role": "user", "content": "Hi"}]
    )
    print(f"Model Used: {raw_response.model}")
    print(f"Usage: {raw_response.usage}")

    # Test Response with Hermes
    print("=== Test Responses ===")
    resp = response_with_hermes(
        message="List the files in the current directory",
        instructions="You are an AI assistant with terminal access."
    )

    # Output Response ID
    print(f"Response ID: {resp.id}")
    print(f"Status: {resp.status}")
    print("\n--- continue (previous_response_id) ---")
    resp_followup = response_with_hermes(
        message="What is in that README.md?",
        previous_response_id=resp.id,
        store=True
        )
