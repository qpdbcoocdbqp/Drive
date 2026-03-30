export default {
  id: "weather-plugin",
  name: "Weather Plugin",
  description: "Weather tool",
  
  register(api) {
    api.registerTool({
      name: "get_weather",
      description: "Get weather information for a specified location",
      parameters: {
        type: "object",
        properties: {
          location: {
            type: "string",
            description: "The city or location name"
          }
        },
        required: ["location"],
        additionalProperties: false
      },
      async execute(_id, params) {
        try {
          const result = await api.runtime.exec({
            command: `python3 /home/node/.openclaw/workspace/tools/get_weather.py "${params.location}"`,
            timeout: 5000,
          });
          
          if (result.exitCode !== 0) {
            return { 
              content: [{ 
                type: "text", 
                text: `Error: ${result.stderr || 'Unknown error'}` 
              }] 
            };
          }
          
          return { 
            content: [{ 
              type: "text", 
              text: result.stdout 
            }] 
          };
        } catch (error) {
          return { 
            content: [{ 
              type: "text", 
              text: `Failed: ${error.message}` 
            }] 
          };
        }
      },
    });
  },
};
