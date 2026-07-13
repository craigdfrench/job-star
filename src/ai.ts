/**
 * Job-Star: AI execution layer
 *
 * The seed version. No router, no model selection, no supervision.
 * Just: call an AI model with the goal + context, return the result.
 *
 * This gets replaced by the full router + supervisor later,
 * but for now it's the simplest thing that works.
 */

export interface AIConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
}

export interface AICallResult {
  content: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
}

export function loadAIConfig(): AIConfig {
  // Priority: GATEHOUSE_API_URL (no key needed) > ANTHROPIC > OPENAI > OPENROUTER

  // Gatehouse-AI — local/self-hosted gateway, may not need an API key
  if (process.env.GATEHOUSE_API_URL) {
    return {
      apiKey: process.env.GATEHOUSE_API_KEY || 'no-key-needed',
      baseUrl: process.env.GATEHOUSE_API_URL,
      model: process.env.JOB_STAR_MODEL || 'ollama/glm-5.2',
    };
  }

  if (process.env.ANTHROPIC_API_KEY) {
    return {
      apiKey: process.env.ANTHROPIC_API_KEY,
      baseUrl: 'https://api.anthropic.com/v1',
      model: process.env.JOB_STAR_MODEL || 'claude-sonnet-4-20250514',
    };
  }

  if (process.env.OPENAI_API_KEY) {
    return {
      apiKey: process.env.OPENAI_API_KEY,
      baseUrl: 'https://api.openai.com/v1',
      model: process.env.JOB_STAR_MODEL || 'gpt-4o',
    };
  }

  if (process.env.OPENROUTER_API_KEY) {
    return {
      apiKey: process.env.OPENROUTER_API_KEY,
      baseUrl: 'https://openrouter.ai/api/v1',
      model: process.env.JOB_STAR_MODEL || 'anthropic/claude-sonnet-4-20250514',
    };
  }

  throw new Error(
    'No AI provider configured. Set one of:\n' +
    '  GATEHOUSE_API_URL=http://gatehouse-ai.craigdfrench.com/v1  (no key needed)\n' +
    '  ANTHROPIC_API_KEY=sk-ant-...\n' +
    '  OPENAI_API_KEY=sk-...\n' +
    '  OPENROUTER_API_KEY=sk-or-...'
  );
}

export async function callAI(
  prompt: string,
  systemPrompt?: string
): Promise<AICallResult> {
  const config = loadAIConfig();

  // Use OpenAI-compatible chat completions format
  // (works with OpenAI, OpenRouter, and many others)
  // For Anthropic, we use the messages API

  const messages: Array<{ role: string; content: string }> = [];
  if (systemPrompt) {
    messages.push({ role: 'system', content: systemPrompt });
  }
  messages.push({ role: 'user', content: prompt });

  const response = await fetch(`${config.baseUrl}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${config.apiKey}`,
      // OpenRouter requires these headers
      ...(config.baseUrl.includes('openrouter') ? {
        'HTTP-Referer': 'https://github.com/gatehouse-ai/job-star',
        'X-Title': 'Job-Star',
      } : {}),
    },
    body: JSON.stringify({
      model: config.model,
      messages,
      max_tokens: 4096,
      temperature: 0.7,
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`AI call failed (${response.status}): ${errorText}`);
  }

  const data = await response.json() as {
    choices: Array<{ message: { content: string } }>;
    usage: { prompt_tokens: number; completion_tokens: number };
    model: string;
  };

  return {
    content: data.choices[0]?.message?.content || '',
    model: data.model || config.model,
    inputTokens: data.usage?.prompt_tokens || 0,
    outputTokens: data.usage?.completion_tokens || 0,
  };
}