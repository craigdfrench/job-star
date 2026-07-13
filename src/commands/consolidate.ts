/**
 * Consolidate duplicate module trees in generated code.
 * 
 * For each component, identifies duplicate module trees, picks the most
 * complete one, merges unique files from the others, and deletes the rest.
 * 
 * Usage: job-star consolidate [--dir ./generated]
 */

import * as fs from 'fs';
import * as path from 'path';

interface TreeInfo {
  root: string;
  fileCount: number;
  totalBytes: number;
  files: string[];
}

function countFiles(dir: string): TreeInfo {
  let files: string[] = [];
  let totalBytes = 0;
  
  function walk(d: string) {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else {
        const stat = fs.statSync(full);
        files.push(path.relative(dir, full));
        totalBytes += stat.size;
      }
    }
  }
  
  if (fs.existsSync(dir)) {
    walk(dir);
  }
  
  return { root: dir, fileCount: files.length, totalBytes, files };
}

function findDuplicateTrees(componentDir: string): { [key: string]: TreeInfo } {
  /**
   * Find duplicate module trees within a component directory.
   * A "module tree" is a directory that contains source files and represents
   * a potential root for the component's module structure.
   * 
   * For example, if a component has:
   *   jobstar/triage/__init__.py, models.py, engine.py
   *   job_star/triage/__init__.py, models.py
   *   triage/__init__.py, classifier.py, engine.py
   * 
   * These are three duplicate trees for the same module.
   */
  
  const trees: { [key: string]: TreeInfo } = {};
  
  // Find all directories that look like module roots
  // A module root typically has __init__.py (Python), lib.rs (Rust), or index.ts (TypeScript)
  function findModuleRoots(dir: string, depth: number = 0) {
    if (depth > 3) return;
    
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      
      // Check if this directory looks like a module root
      const entries = fs.readdirSync(full);
      const hasInit = entries.some(e => 
        e === '__init__.py' || e === 'lib.rs' || e === 'index.ts' || e === 'index.js' ||
        e === 'mod.rs' || e === '__init__.ts'
      );
      const hasSourceFiles = entries.some(e => 
        /\.(py|rs|ts|js|jsx|tsx)$/.test(e)
      );
      
      if (hasInit && hasSourceFiles) {
        // This looks like a module root
        const info = countFiles(full);
        const moduleName = entry.name; // e.g., "triage", "router", "supervisor"
        if (!trees[moduleName] || info.fileCount > trees[moduleName].fileCount) {
          trees[moduleName] = info;
        }
      }
      
      // Recurse into subdirectories
      findModuleRoots(full, depth + 1);
    }
  }
  
  // Also check root level
  const rootEntries = fs.readdirSync(componentDir, { withFileTypes: true });
  const rootHasInit = rootEntries.some(e => 
    e.name === '__init__.py' || e.name === 'lib.rs' || e.name === 'index.ts'
  );
  const rootHasSource = rootEntries.some(e => 
    /\.(py|rs|ts|js|jsx|tsx)$/.test(e.name) && e.isFile()
  );
  if (rootHasInit && rootHasSource) {
    const info = countFiles(componentDir);
    trees['__root__'] = info;
  }
  
  findModuleRoots(componentDir);
  return trees;
}

function mergeTrees(source: string, target: string): { merged: number; skipped: number } {
  let merged = 0;
  let skipped = 0;
  
  function walk(src: string, tgt: string) {
    for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
      const srcFull = path.join(src, entry.name);
      const tgtFull = path.join(tgt, entry.name);
      
      if (entry.isDirectory()) {
        if (!fs.existsSync(tgtFull)) {
          // Copy entire directory
          fs.mkdirSync(tgtFull, { recursive: true });
        }
        walk(srcFull, tgtFull);
      } else {
        if (fs.existsSync(tgtFull)) {
          // File exists in target — compare content
          const srcContent = fs.readFileSync(srcFull, 'utf-8');
          const tgtContent = fs.readFileSync(tgtFull, 'utf-8');
          if (srcContent === tgtContent) {
            skipped++;
          } else if (tgtContent.length < srcContent.length) {
            // Source is larger — might be more complete. Replace.
            fs.copyFileSync(srcFull, tgtFull);
            merged++;
          } else {
            // Target is larger — keep it, skip source
            skipped++;
          }
        } else {
          // File doesn't exist in target — copy it
          fs.mkdirSync(path.dirname(tgtFull), { recursive: true });
          fs.copyFileSync(srcFull, tgtFull);
          merged++;
        }
      }
    }
  }
  
  walk(source, target);
  return { merged, skipped };
}

function removeDir(dir: string) {
  if (!fs.existsSync(dir)) return;
  fs.rmSync(dir, { recursive: true, force: true });
}

function consolidateComponent(componentDir: string): void {
  const componentName = path.basename(componentDir);
  console.log(`\n  📦 ${componentName}`);
  
  // Step 1: Find all module trees
  const trees = findDuplicateTrees(componentDir);
  const treeList = Object.entries(trees).map(([name, info]) => ({ name, ...info }));
  
  if (treeList.length <= 1) {
    console.log(`     ✅ No duplicates found (${treeList.length} tree)`);
    return;
  }
  
  console.log(`     Found ${treeList.length} potential module trees:`);
  treeList.sort((a, b) => b.fileCount - a.fileCount);
  treeList.forEach((t, i) => {
    const marker = i === 0 ? ' 👑 KEEP' : '    merge';
    console.log(`     ${marker} ${t.name}: ${t.fileCount} files, ${(t.totalBytes / 1024).toFixed(1)} KB`);
  });
  
  // Step 2: Pick the tree with the most files as the winner
  const winner = treeList[0];
  
  // Step 3: Merge unique files from losers into winner
  for (let i = 1; i < treeList.length; i++) {
    const loser = treeList[i];
    const result = mergeTrees(loser.root, winner.root);
    console.log(`     Merged ${result.merged} files from ${loser.name} (skipped ${result.skipped} duplicates)`);
  }
  
  // Step 4: Remove the losing trees
  for (let i = 1; i < treeList.length; i++) {
    const loser = treeList[i];
    if (loser.name === '__root__') continue; // Don't remove root
    removeDir(loser.root);
    console.log(`     🗑  Removed ${loser.name}/`);
  }
  
  // Step 5: Clean up block_N files (unnamed code blocks from extraction)
  const allFiles = countFiles(componentDir);
  const blockFiles = allFiles.files.filter(f => /^block_\d+\./.test(f));
  for (const bf of blockFiles) {
    const full = path.join(componentDir, bf);
    fs.unlinkSync(full);
    console.log(`     🗑  Removed ${bf} (unnamed block)`);
  }
  
  console.log(`     ✅ Consolidated to ${winner.name}/ with ${countFiles(winner.root).fileCount} files`);
}

// Main
const args = process.argv.slice(2);
const dirIdx = args.indexOf('--dir');
const baseDir = dirIdx >= 0 ? args[dirIdx + 1] : './generated';

if (!fs.existsSync(baseDir)) {
  console.error(`Directory not found: ${baseDir}`);
  process.exit(1);
}

console.log('═══════════════════════════════════════════════════════════');
console.log('CONSOLIDATING GENERATED CODE');
console.log('═══════════════════════════════════════════════════════════');
console.log(`Base directory: ${baseDir}`);

// Find all component directories
const components = fs.readdirSync(baseDir, { withFileTypes: true })
  .filter(e => e.isDirectory())
  .map(e => path.join(baseDir, e.name))
  .sort();

console.log(`Found ${components.length} components`);

for (const comp of components) {
  consolidateComponent(comp);
}

console.log('\n═══════════════════════════════════════════════════════════');
console.log('CONSOLIDATION COMPLETE');
console.log('═══════════════════════════════════════════════════════════');

// Final count
let totalFiles = 0;
for (const comp of components) {
  totalFiles += countFiles(comp).fileCount;
}
console.log(`Total files: ${totalFiles}`);