import openai


base_url = "http://localhost:18789/v1"
api_key = "***"
default_headers={
    'Authorization': 'Bearer 9a45764a5de11264d2e4d49349533989cbd49c03ed82240e'
}

client = openai.OpenAI(
    base_url=base_url,
    api_key=api_key,
    default_headers=default_headers
)

client.models.list()

# test completions feature
response = client.chat.completions.create(
    model="openclaw/default",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ],
    max_tokens=100,
)
print(response.choices[0].message.content)

# test completions feature with stream
response = client.chat.completions.create(
    model="openclaw/default",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ],
    max_tokens=100,
    stream=True
)
for chunk in response:
    print(chunk.choices[0].delta.content)

# test embedding feature
response = client.embeddings.create(
    extra_headers={'x-openclaw-model': 'encoder'},
    model="openclaw/default",
    input=["alpha", "beta"],
)
len(response.data)
len(response.data[0].embedding)

# test responses feature
response = client.responses.create(
    model="openclaw/default",
    input="Hello, how are you?",
    )

print(response.output_text)

# test get_weather tool
response = client.chat.completions.create(
    model="openclaw/default",
    messages=[
        {"role": "user", "content": "What is the weather in Montreal?"},
    ],
    max_tokens=100,
)

print(response.choices[0].message.content)
