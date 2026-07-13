/**
 * Extract generated code from completed goal steps and write to files.
 *
 * Usage: job-star extract <goal-id> [--output ./generated]
 *
 * Reads all completed steps for a goal, extracts code blocks from
 * the AI output, and writes them to actual files.
 */

import { query, type GoalRow, type StepRow } from '../db.ts';
import * as fs from 'fs';
import * as path from 'path';

interface CodeBlock {
  language: string;
  filename: string;
  content: string;
}

/**
 * Extract code blocks from AI output text.
 * The AI typically outputs code in markdown format:
 *   ## File: `path/to/file.py`
 *   ```python
 *   ...code...
 *   ```
 *
 * Or sometimes:
 *   ```python
 *   ...code...
 *   ```
 *   **Files to create:** `path/to/file.py`
 */
function extractCodeBlocks(text: string): CodeBlock[] {
  const blocks: CodeBlock[] = [];
  
  // Pattern 1: "## File: `path`" followed by ```lang ... ```
  const fileHeaderPattern = /(?:##\s*)?(?:\*\*)?File(?:s)?:?\s*\*?\*?`?([^`\n*]+)`?\*?\*?\s*\n+```(\w+)\n([\s\S]*?)```/g;
  
  // Pattern 2: ```lang ... ``` with "Files to create:" before or after
  const codeBlockPattern = /```(\w+)\n([\s\S]*?)```/g;
  
  // Pattern 3: "Files to create/modify:" section
  const filesSectionPattern = /(?:Files to (?:create|modify|create\/modify)[:\s]*\n)((?:[-*]\s*`[^`]+`\s*.*\n?)+)/gi;
  
  let match: RegExpExecArray | null;
  
  // First pass: extract file-header + code block pairs
  const usedRanges: [number, number][] = [];
  
  while ((match = fileHeaderPattern.exec(text)) !== null) {
    const filePath = match[1].trim().replace(/`/g, '');
    const language = match[2];
    const content = match[3];
    
    usedRanges.push([match.index, match.index + match[0].length]);
    
    blocks.push({
      language,
      filename: filePath,
      content: content.trimEnd() + '\n',
    });
  }
  
  // Second pass: find code blocks that weren't paired with file headers
  // and try to find nearby file references
  while ((match = codeBlockPattern.exec(text)) !== null) {
    // Check if this block was already captured
    const start = match.index;
    const end = match.index + match[0].length;
    const alreadyUsed = usedRanges.some(([s, e]) => start >= s && end <= e);
    
    if (alreadyUsed) continue;
    
    const language = match[1];
    const content = match[2];
    
    // Look backwards for a file path reference
    const beforeBlock = text.substring(Math.max(0, start - 500), start);
    const filePathMatch = beforeBlock.match(/[`']([^`'\n]+\.\w+)[`']/g);
    
    // Look forwards too
    const afterBlock = text.substring(end, Math.min(text.length, end + 500));
    const afterFilePathMatch = afterBlock.match(/[`']([^`'\n]+\.\w+)[`']/g);
    
    let filename: string | null = null;
    
    // Try to find a "Files to create:" reference
    const filesMatch = afterBlock.match(/(?:Files to (?:create|modify)[:\s]*\n)([-*]\s*`([^`]+)`)/i);
    if (filesMatch) {
      filename = filesMatch[2];
    }
    
    if (!filename && filePathMatch) {
      // Take the last file path before the block
      const paths = filePathMatch.map(m => m.replace(/[`']/g, ''));
      // Prefer paths that look like code files
      const codePath = paths.find(p => /\.(py|rs|ts|js|go|java|rb|sh|yaml|yml|json|toml|md|sql)$/i.test(p));
      filename = codePath || paths[paths.length - 1];
    }
    
    if (!filename && afterFilePathMatch) {
      const paths = afterFilePathMatch.map(m => m.replace(/[`']/g, ''));
      const codePath = paths.find(p => /\.(py|rs|ts|js|go|java|rb|sh|yaml|yml|json|toml|md|sql)$/i.test(p));
      filename = codePath || null;
    }
    
    // If no filename found, generate one based on language and block index
    if (!filename) {
      const ext = languageToExt(language);
      const idx = blocks.filter(b => b.language === language).length + 1;
      filename = `block_${idx}.${ext}`;
    }
    
    // Clean up the filename
    filename = filename.replace(/^\.\//, '').replace(/^src\//, '').replace(/^job_star\//, '');
    
    blocks.push({
      language,
      filename,
      content: content.trimEnd() + '\n',
    });
  }
  
  return blocks;
}

function languageToExt(lang: string): string {
  const map: Record<string, string> = {
    python: 'py',
    py: 'py',
    rust: 'rs',
    rs: 'rs',
    typescript: 'ts',
    ts: 'ts',
    javascript: 'js',
    js: 'js',
    go: 'go',
    java: 'java',
    ruby: 'rb',
    sh: 'sh',
    bash: 'sh',
    shell: 'sh',
    yaml: 'yaml',
    yml: 'yaml',
    json: 'json',
    toml: 'toml',
    sql: 'sql',
    markdown: 'md',
    md: 'md',
    html: 'html',
    css: 'css',
    dockerfile: 'dockerfile',
  };
  return map[lang.toLowerCase()] || 'txt';
}

async function extractGoal(goalId: string, outputDir: string): Promise<void> {
  // Resolve partial UUID
  const goals = await query<GoalRow>(
    `SELECT * FROM goals WHERE id::text LIKE $1`,
    [`${goalId}%`]
  );
  
  if (goals.length === 0) {
    console.error(`Goal not found: ${goalId}`);
    process.exit(1);
  }
  
  const goal = goals[0];
  const fullId = goal.id;
  
  const steps = await query<StepRow>(
    `SELECT * FROM goal_steps WHERE goal_id = $1 AND status = 'completed' ORDER BY order_index`,
    [fullId]
  );
  
  if (steps.length === 0) {
    console.error(`No completed steps found for goal: ${goal.title}`);
    process.exit(1);
  }
  
  // Clean goal title for directory name
  const goalDir = goal.title
    .replace(/^Build Job-Star:\s*/i, '')
    .replace(/[^a-z0-9]+/gi, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
  
  const basePath = path.join(outputDir, goalDir);
  
  console.log(`\n  Extracting: ${goal.title}`);
  console.log(`  Output:     ${basePath}`);
  console.log(`  Steps:      ${steps.length} completed`);
  console.log();
  
  let totalFiles = 0;
  let totalBytes = 0;
  
  for (const step of steps) {
    if (!step.result) continue;
    
    const result = step.result as { content?: string };
    const content = result.content || '';
    
    const blocks = extractCodeBlocks(content);
    
    if (blocks.length === 0) {
      // No code blocks found — save the raw text
      const stepDir = path.join(basePath, 'steps');
      fs.mkdirSync(stepDir, { recursive: true });
      const stepFile = path.join(stepDir, `step_${step.order_index}_${step.title.replace(/[^a-z0-9]+/gi, '_').toLowerCase().substring(0, 40)}.md`);
      fs.writeFileSync(stepFile, content);
      totalFiles++;
      totalBytes += content.length;
      console.log(`    Step ${step.order_index}: ${step.title} → ${path.relative(outputDir, stepFile)} (raw text, no code blocks)`);
      continue;
    }
    
    for (const block of blocks) {
      const filePath = path.join(basePath, block.filename);
      const dir = path.dirname(filePath);
      fs.mkdirSync(dir, { recursive: true });
      
      // Don't overwrite if file already exists and content is identical
      if (fs.existsSync(filePath)) {
        const existing = fs.readFileSync(filePath, 'utf-8');
        if (existing === block.content) {
          continue; // Skip identical
        }
        // Append with separator if different
        fs.appendFileSync(filePath, `\n\n// --- DUPLICATE BLOCK ---\n\n${block.content}`);
      } else {
        fs.writeFileSync(filePath, block.content);
      }
      
      totalFiles++;
      totalBytes += block.content.length;
      console.log(`    Step ${step.order_index}: ${step.title} → ${path.relative(outputDir, filePath)}`);
    }
  }
  
  console.log();
  console.log(`  ✅ Extracted ${totalFiles} files (${(totalBytes / 1024).toFixed(1)} KB)`);
  console.log();
}

// Main
const args = process.argv.slice(2);
const goalId = args[0];
const outputIdx = args.indexOf('--output');
const outputDir = outputIdx >= 0 ? args[outputIdx + 1] : './generated';

if (!goalId) {
  console.error('Usage: job-star extract <goal-id> [--output ./generated]');
  process.exit(1);
}

extractGoal(goalId, outputDir).catch(err => {
  console.error(`Error: ${err.message}`);
  process.exit(1);
});