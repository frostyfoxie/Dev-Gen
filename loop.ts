// backend/src/agent/loop.ts
import { GoogleGenAI } from '@google/genai';
import { GeminiKeyRotator } from '../utils/keyRotator';
import { agentToolDeclarations } from './tools';

export class AgentLoop {
  private keyRotator: GeminiKeyRotator;
  private modelName: string = 'gemini-2.5-flash';

  constructor(apiKeys: string[]) {
    this.keyRotator = new GeminiKeyRotator(apiKeys);
  }

  /**
   * Executes the autonomous multi-turn tool-calling feedback loop.
   */
  public async runTask(userPrompt: string, executeToolCallback: (name: string, args: any) => Promise<string>): Promise<string> {
    
    // Initial system persona instructions
    const systemInstruction = `You are an elite autonomous software engineering agent. 
    You have direct access to local workspace tools (list_files, read_file, write_file, edit_file_diff, execute_command).
    Break down user goals methodically, inspect files, apply edits, and execute test commands to verify your work.`;

    let conversationHistory: any[] = [
      { role: 'user', parts: [{ text: userPrompt }] }
    ];

    let maxSteps = 10; // Safety guardrail against endless loops
    let step = 0;

    while (step < maxSteps) {
      step++;

      // Execute Gemini call through the key rotator wrapper to safeguard against 429 errors
      const response = await this.keyRotator.executeWithRotation(async (client: GoogleGenAI) => {
        return await client.models.generateContent({
          model: this.modelName,
          contents: conversationHistory,
          config: {
            systemInstruction: systemInstruction,
            tools: agentToolDeclarations,
            temperature: 0.2,
          }
        });
      });

      // Check if model wants to invoke tools
      const functionCalls = response.functionCalls;
      const modelText = response.text;

      if (modelText) {
        conversationHistory.push({ role: 'model', parts: [{ text: modelText }] });
      }

      if (!functionCalls || functionCalls.length === 0) {
        // Agent finished execution and returned final response text
        return modelText || "Task completed successfully with no textual output.";
      }

      // Process tool calls requested by Gemini
      for (const call of functionCalls) {
        console.log(`[Agent Loop] Executing Tool: ${call.name} with args:`, JSON.stringify(call.args));
        
        let toolOutput: string;
        try {
          toolOutput = await executeToolCallback(call.name, call.args);
        } catch (error: any) {
          toolOutput = `Error executing tool ${call.name}: ${error.message}`;
        }

        // Push model function call and function response back to history
        conversationHistory.push({
          role: 'model',
          parts: [{ functionCall: call }]
        });

        conversationHistory.push({
          role: 'user',
          parts: [{
            functionResponse: {
              name: call.name,
              response: { result: toolOutput }
            }
          }]
        });
      }
    }

    return "Agent reached maximum step iteration limit without full termination.";
  }
}
