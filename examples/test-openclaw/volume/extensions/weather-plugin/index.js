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
        // Mock weather data - in production, you'd call a real weather API
        const weatherData = {
          location: params.location,
          temperature: "22°C",
          condition: "Partly Cloudy",
          humidity: "65%",
          wind: "10 km/h",
          forecast: "Clear skies expected"
        };
        
        const result = `Weather in ${weatherData.location}: ${weatherData.temperature}, ${weatherData.condition}, Humidity ${weatherData.humidity}, Wind ${weatherData.wind}`;
        
        return { 
          content: [{ 
            type: "text", 
            text: result 
          }] 
        };
      },
    });
  },
};
