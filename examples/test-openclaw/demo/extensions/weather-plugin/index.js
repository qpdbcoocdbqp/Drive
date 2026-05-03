import { execFile } from 'child_process';
import util from 'util';
import { fileURLToPath } from 'url';
import path from 'path';

const execFilePromise = util.promisify(execFile);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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
          const scriptPath = path.resolve(__dirname, '../../workspace/tools/get_weather.py');

          let stdoutResult;
          try {
            // Linux/Docker try python3
            const { stdout } = await execFilePromise("python3", [scriptPath, params.location]);
            stdoutResult = stdout;
          } catch (err3) {
            try {
              // if python3 not exist, fallback to python
              const { stdout } = await execFilePromise("python", [scriptPath, params.location]);
              stdoutResult = stdout;
            } catch (err) {
              throw new Error(`Python execution failed. python3 error: ${err3.message} | python error: ${err.message}. Please verify Python is installed in this runtime environment.`);
            }
          }

          const result = stdoutResult.trim();

          return {
            content: [{
              type: "text",
              text: result
            }]
          };
        } catch (error) {
          return {
            content: [{
              type: "text",
              text: `Error: ${error.message}`
            }]
          };
        }
      },
    });
  },
};
