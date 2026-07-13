/**
 * Tests for CompetingResourceDetector
 */

import { ResourceRegistry } from "../resource_registry";
import { ResourceParser } from "../resource_parser";
import { CompetingResourceDetector, GoalForDetection } from "../competing_resource";

function makeRegistry(): ResourceRegistry {
  return new ResourceRegistry({
    entries: [
      {
        id: "time-daily",
        name: "daily time",
        category: "time",
        capacity: 8,
        unit: "hours",
        scarcity: 0.7,
        aliases: ["time", "hours"],
      },
      {
        id: "money-budget",
        name: "budget",
        category: "money",
        capacity: 5000,
        unit: "USD",
        scarcity: 0.8,
        aliases: ["budget", "money"],
      },
      {
        id: "person-alice",
        name: "Alice",
        category: "person",
        capacity: 3,
        unit: "interactions/week",
        scarcity: 0.6,
        aliases: ["@alice", "alice"],
      },
    ],
  });
}

describe("CompetingResourceDetector", () => {
  let registry: ResourceRegistry;
  let detector: CompetingResourceDetector;

  beforeEach(() => {
    registry = makeRegistry();
    detector = new CompetingResourceDetector({ registry });
  });

  test("detects two goals competing for time", () => {
    const goals: GoalForDetection[] = [
      { id: "g1", text: "Spend 5 hours reviewing PRs" },
      { id: "g2", text: "Write documentation for 4 hours" },
    ];

    const conflicts = detector.detect(goals);

    expect(conflicts.length).toBeGreaterThanOrEqual(1);
    const timeConflict = conflicts.find((c) => c.resourceCategory === "time");
    expect(timeConflict).toBeDefined();
    expect(timeConflict!.goalIds).toContain("g1");
    expect(timeConflict!.goalIds).toContain("g2");
    expect(timeConflict!.totalDemand).toBe(9);
    expect(timeConflict!.capacity).toBe(8);
    // 9 hours demand vs 8 capacity → ratio = 1.0, severity = 0.7 * 1.0 = 0.7
    expect(timeConflict!.severity).toBeCloseTo(0.7, 1);
  });

  test("detects two goals competing for money", () => {
    const goals: GoalForDetection[] = [
      { id: "g1", text: "Buy new laptop for $3000 from budget" },
      { id: "g2", text: "Purchase software licenses for $2500" },
    ];

    const conflicts = detector.detect(goals);
    const moneyConflict = conflicts.find((c) => c.resourceCategory === "money");

    expect(moneyConflict).toBeDefined();
    expect(moneyConflict!.totalDemand).toBe(5500);
    expect(moneyConflict!.capacity).toBe(5000);
    // 5500 / 5000 = 1.1 → capped at 1.0, severity = 0.8 * 1.0 = 0.8
    expect(moneyConflict!.severity).toBeCloseTo(0.8, 1);
  });

  test("detects two goals competing for the same person", () => {
    const goals: GoalForDetection[] = [
      { id: "g1", text: "Schedule meeting with @alice for review" },
      { id: "g2", text: "Ask @alice for feedback on the proposal" },
    ];

    const conflicts = detector.detect(goals);
    const personConflict = conflicts.find(
      (c) => c.resourceCategory === "person"
    );

    expect(personConflict).toBeDefined();
    expect(personConflict!.resourceLabel).toMatch(/alice/i);
  });

  test("does not flag unrelated goals", () => {
    const goals: GoalForDetection[] = [
      { id: "g1", text: "Read a book about gardening" },
      { id: "g2", text: "Clean the kitchen" },
    ];

    const conflicts = detector.detect(goals);
    // These goals don't mention any registered resources or patterns
    expect(conflicts.length).toBe(0);
  });

  test("respects minSeverity filter", () => {
    const lowSeverityDetector = new CompetingResourceDetector({
      registry,
      minSeverity: 0.95,
    });

    const goals: GoalForDetection[] = [
      { id: "g1", text: "Spend 1 hour on email" },
      { id: "g2", text: "Spend 1 hour on reading" },
    ];

    const conflicts = lowSeverityDetector.detect(goals);
    // 2 hours / 8 capacity = 0.25 ratio * 0.7 scarcity = 0.175 severity
    // This is below 0.95 threshold
    expect(conflicts.length).toBe(0);
  });

  test("sorts conflicts by severity descending", () => {
    const goals: GoalForDetection[] = [
      { id: "g1", text: "Spend 6 hours on project A" },
      { id: "g2", text: "Spend 5 hours on project B" },
      { id: "g3", text: "Spend 1 hour on email" },
    ];

    const conflicts = detector.detect(goals);
    for (let i = 1; i < conflicts.length; i++) {
      expect(conflicts[i].severity).toBeLessThanOrEqual(
        conflicts[i - 1].severity
      );
    }
  });
});

describe("ResourceParser", () => {
  test("extracts time references", () => {
    const parser = new ResourceParser();
    const refs = parser.parse("Spend 3.5 hours on the task");
    const timeRef = refs.find((r) => r.category === "time");
    expect(timeRef).toBeDefined();
    expect(timeRef!.estimatedDemand).toBeCloseTo(3.5, 1);
  });

  test("extracts money references", () => {
    const parser = new ResourceParser();
    const refs = parser.parse("Budget $1,500 for the conference");
    const moneyRef = refs.find((r) => r.category === "money");
    expect(moneyRef).toBeDefined();
    expect(moneyRef!.estimatedDemand).toBe(1500);
  });

  test("extracts person mentions", () => {
    const parser = new ResourceParser();
    const refs = parser.parse("Check with @bob_smith about the deadline");
    const personRef = refs.find((r) => r.category === "person");
    expect(personRef).toBeDefined();
    expect(personRef!.label).toBe("@bob_smith");
  });

  test("extracts file references", () => {
    const parser = new ResourceParser();
    const refs = parser.parse("Update src/index.ts and config.yaml");
    const fileRefs = refs.filter((r) => r.category === "file");
    expect(fileRefs.length).toBeGreaterThanOrEqual(2);
  });
});

describe("ResourceRegistry", () => {
  test("register and retrieve", () => {
    const reg = new ResourceRegistry();
    reg.register({
      id: "test-1",
      name: "Test Resource",
      category: "custom",
      capacity: 10,
      unit: "units",
      scarcity: 0.5,
    });
    expect(reg.get("test-1")).toBeDefined();
    expect(reg.get("test-1")!.name).toBe("Test Resource");
  });

  test("findByName matches aliases", () => {
    const reg = new ResourceRegistry({
      entries: [
        {
          id: "x",
          name: "Example",
          category: "custom",
          capacity: 1,
          unit: "units",
          scarcity: 0.3,
          aliases: ["ex", "sample"],
        },
      ],
    });
    expect(reg.findByName("sample")).toBeDefined();
    expect(reg.findByName("nonexistent")).toBeUndefined();
  });

  test("findInText returns matching resources", () => {
    const reg = new ResourceRegistry({
      entries: [
        {
          id: "db",
          name: "database",
          category: "equipment",
          capacity: 1,
          unit: "instances",
          scarcity: 0.4,
          aliases: ["postgres", "db"],
        },
      ],
    });
    const matches = reg.findInText("Connect to the postgres instance");
    expect(matches.length).toBe(1);
    expect(matches[0].id).toBe("db");
  });

  test("rejects invalid scarcity", () => {
    const reg = new ResourceRegistry();
    expect(() =>
      reg.register({
        id: "bad",
        name: "Bad",
        category: "custom",
        capacity: 1,
        unit: "x",
        scarcity: 1.5,
      })
    ).toThrow();
  });

  test("withDefaults creates common resources", () => {
    const reg = ResourceRegistry.withDefaults();
    expect(reg.getAll().length).toBeGreaterThan(0);
    expect(reg.get("time-daily")).toBeDefined();
    expect(reg.get("money-budget")).toBeDefined();
  });
});
