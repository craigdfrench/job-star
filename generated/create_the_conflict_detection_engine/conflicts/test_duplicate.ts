/**
 * src/conflicts/detectors/test_duplicate.ts
 *
 * Tests for DuplicateDetector.
 * Run with: npx ts-node src/conflicts/detectors/test_duplicate.ts
 */

import { DuplicateDetector, type Goal } from './duplicate';

function assert(condition: boolean, message: string): void {
  if (!condition) {
    console.error(`❌ FAIL: ${message}`);
    process.exitCode = 1;
  } else {
    console.log(`✅ PASS: ${message}`);
  }
}

async function runTests(): Promise<void> {
  console.log('\n=== DuplicateDetector Tests ===\n');

  const detector = new DuplicateDetector({ threshold: 0.75 });

  // --- Test 1: Exact duplicate ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Learn TypeScript', description: 'Master TypeScript for backend development' },
      { id: 'g2', title: 'Learn TypeScript', description: 'Master TypeScript for backend development' },
    ];
    const conflicts = await detector.detect(goals);
    assert(conflicts.length === 1, 'Exact duplicate detected');
    assert(conflicts[0]?.severity === 'critical', 'Exact duplicate is critical severity');
    assert(conflicts[0]?.confidence > 0.95, 'Exact duplicate confidence > 0.95');
  }

  // --- Test 2: Near-duplicate (reworded) ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Build a REST API', description: 'Create a REST API with Node.js and Express' },
      { id: 'g2', title: 'Create REST API service', description: 'Build REST API using Node Express framework' },
    ];
    const conflicts = await detector.detect(goals);
    assert(conflicts.length >= 1, 'Near-duplicate (reworded) detected');
  }

  // --- Test 3: Non-duplicate ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Learn TypeScript', description: 'Study TypeScript language features' },
      { id: 'g2', title: 'Run a marathon', description: 'Train for and complete a 26.2 mile race' },
    ];
    const conflicts = await detector.detect(goals);
    assert(conflicts.length === 0, 'Unrelated goals not flagged as duplicates');
  }

  // --- Test 4: Cross-domain duplicate ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Improve coding skills', description: 'Practice programming daily', domain: 'work' },
      { id: 'g2', title: 'Improve coding skills', description: 'Practice programming daily', domain: 'personal' },
    ];
    const conflicts = await detector.detect(goals);
    assert(conflicts.length === 1, 'Cross-domain duplicate detected');
  }

  // --- Test 5: Cross-domain disabled ---
  {
    const detectorNoCross = new DuplicateDetector({ threshold: 0.75, crossDomain: false });
    const goals: Goal[] = [
      { id: 'g1', title: 'Improve coding skills', description: 'Practice programming daily', domain: 'work' },
      { id: 'g2', title: 'Improve coding skills', description: 'Practice programming daily', domain: 'personal' },
    ];
    const conflicts = await detectorNoCross.detect(goals);
    assert(conflicts.length === 0, 'Cross-domain disabled: no conflict across domains');
  }

  // --- Test 6: Empty / single goal ---
  {
    assert((await detector.detect([])).length === 0, 'Empty goals list yields no conflicts');
    assert((await detector.detect([{ id: 'g1', title: 'Solo' }])).length === 0, 'Single goal yields no conflicts');
  }

  // --- Test 7: Confidence proportional to similarity ---
  {
    const exactGoals: Goal[] = [
      { id: 'g1', title: 'Read more books', description: 'Read 12 books this year' },
      { id: 'g2', title: 'Read more books', description: 'Read 12 books this year' },
    ];
    const partialGoals: Goal[] = [
      { id: 'g1', title: 'Read more books', description: 'Read 12 books this year' },
      { id: 'g2', title: 'Read books regularly', description: 'Read novels monthly' },
    ];
    const exactConflicts = await detector.detect(exactGoals);
    const partialConflicts = await detector.detect(partialGoals);
    if (exactConflicts.length > 0 && partialConflicts.length > 0) {
      assert(
        exactConflicts[0].confidence > partialConflicts[0].confidence,
        'Exact duplicate has higher confidence than partial',
      );
    }
  }

  // --- Test 8: Evidence contains matched tokens ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Learn Python programming', description: 'Study Python syntax' },
      { id: 'g2', title: 'Learn Python programming', description: 'Study Python syntax' },
    ];
    const conflicts = await detector.detect(goals);
    assert(
      (conflicts[0]?.evidence?.matchedTokens?.length ?? 0) > 0,
      'Evidence contains matched tokens',
    );
    assert(
      conflicts[0]?.evidence?.threshold === 0.75,
      'Evidence records threshold',
    );
  }

  // --- Test 9: Tags contribute to similarity ---
  {
    const goals: Goal[] = [
      { id: 'g1', title: 'Exercise plan', description: 'Workout routine', tags: ['fitness', 'health', 'daily'] },
      { id: 'g2', title: 'Exercise schedule', description: 'Training routine', tags: ['fitness', 'health', 'weekly'] },
    ];
    const detectorWithTags = new DuplicateDetector({ threshold: 0.6, includeTags: true });
    const detectorNoTags = new DuplicateDetector({ threshold: 0.6, includeTags: false });
    const withTags = await detectorWithTags.detect(goals);
    const withoutTags = await detectorNoTags.detect(goals);
    // With tags, similarity should be >= without tags
    if (withTags.length > 0 && withoutTags.length > 0) {
      assert(
        withTags[0].confidence >= withoutTags[0].confidence,
        'Tags increase or maintain similarity score',
      );
    }
  }

  console.log('\n=== Tests complete ===\n');
}

runTests().catch((err) => {
  console.error('Test runner error:', err);
  process.exitCode = 1;
});
