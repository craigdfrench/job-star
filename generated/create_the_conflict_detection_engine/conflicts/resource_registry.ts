/**
 * Resource Registry
 *
 * Maintains a catalog of known limited resources and their capacities.
 * Resources can be time, money, attention, specific files, specific people,
 * equipment, or any other scarce asset that goals might compete for.
 *
 * Each resource has:
 *  - A unique ID
 *  - A human-readable name
 *  - A category (time, money, attention, file, person, equipment, custom)
 *  - A capacity (total available amount)
 *  - A unit (hours, dollars, sessions, etc.)
 *  - A scarcity weight (0.0–1.0) indicating how limited/precious this resource is
 *  - Optional aliases for matching in goal text
 */

export type ResourceCategory =
  | "time"
  | "money"
  | "attention"
  | "file"
  | "person"
  | "equipment"
  | "custom";

export interface ResourceEntry {
  id: string;
  name: string;
  category: ResourceCategory;
  /** Total available capacity of this resource. */
  capacity: number;
  /** Unit of measurement (e.g., "hours", "USD", "sessions"). */
  unit: string;
  /**
   * Scarcity weight from 0.0 (abundant) to 1.0 (extremely scarce).
   * Used to scale conflict severity when multiple goals demand this resource.
   */
  scarcity: number;
  /** Alternative names / patterns that should match this resource in goal text. */
  aliases?: string[];
  /** Optional domain this resource belongs to (e.g., "work", "personal"). */
  domain?: string;
  /** Free-form metadata. */
  metadata?: Record<string, unknown>;
}

export interface ResourceRegistryOptions {
  /** Initial entries to seed the registry. */
  entries?: ResourceEntry[];
}

export class ResourceRegistry {
  private entries: Map<string, ResourceEntry> = new Map();

  constructor(options: ResourceRegistryOptions = {}) {
    if (options.entries) {
      for (const entry of options.entries) {
        this.register(entry);
      }
    }
  }

  /**
   * Register a new resource or update an existing one by ID.
   */
  register(entry: ResourceEntry): void {
    if (!entry.id || entry.id.trim() === "") {
      throw new Error("Resource entry must have a non-empty id");
    }
    if (entry.scarcity < 0 || entry.scarcity > 1) {
      throw new Error(
        `Resource "${entry.id}" scarcity must be between 0.0 and 1.0, got ${entry.scarcity}`
      );
    }
    this.entries.set(entry.id, { ...entry });
  }

  /**
   * Remove a resource by ID.
   */
  unregister(id: string): boolean {
    return this.entries.delete(id);
  }

  /**
   * Get a resource entry by ID.
   */
  get(id: string): ResourceEntry | undefined {
    return this.entries.get(id);
  }

  /**
   * Get all registered resources.
   */
  getAll(): ResourceEntry[] {
    return Array.from(this.entries.values());
  }

  /**
   * Get all resources in a given category.
   */
  getByCategory(category: ResourceCategory): ResourceEntry[] {
    return this.getAll().filter((e) => e.category === category);
  }

  /**
   * Look up a resource by name or alias (case-insensitive).
   * Returns the first match or undefined.
   */
  findByName(name: string): ResourceEntry | undefined {
    const lower = name.toLowerCase().trim();
    for (const entry of this.entries.values()) {
      if (entry.name.toLowerCase() === lower) {
        return entry;
      }
      if (entry.aliases) {
        for (const alias of entry.aliases) {
          if (alias.toLowerCase() === lower) {
            return entry;
          }
        }
      }
    }
    return undefined;
  }

  /**
   * Find all resources whose name or alias appears in the given text
   * (case-insensitive substring match).
   */
  findInText(text: string): ResourceEntry[] {
    const lower = text.toLowerCase();
    const matches: ResourceEntry[] = [];
    for (const entry of this.entries.values()) {
      const candidates = [entry.name, ...(entry.aliases ?? [])];
      for (const candidate of candidates) {
        if (lower.includes(candidate.toLowerCase())) {
          matches.push(entry);
          break; // don't double-count the same entry
        }
      }
    }
    return matches;
  }

  /**
   * Create a registry pre-populated with common default resources.
   */
  static withDefaults(): ResourceRegistry {
    return new ResourceRegistry({
      entries: [
        {
          id: "time-daily",
          name: "daily time",
          category: "time",
          capacity: 16,
          unit: "hours",
          scarcity: 0.7,
          aliases: ["time", "hours", "day", "today"],
        },
        {
          id: "time-weekly",
          name: "weekly time",
          category: "time",
          capacity: 80,
          unit: "hours",
          scarcity: 0.5,
          aliases: ["week", "this week"],
        },
        {
          id: "money-budget",
          name: "budget",
          category: "money",
          capacity: 10000,
          unit: "USD",
          scarcity: 0.8,
          aliases: ["money", "funds", "budget", "cost", "spending"],
        },
        {
          id: "attention-focus",
          name: "focused attention",
          category: "attention",
          capacity: 4,
          unit: "deep-work sessions",
          scarcity: 0.9,
          aliases: ["attention", "focus", "concentration", "deep work"],
        },
        {
          id: "person-manager",
          name: "manager",
          category: "person",
          capacity: 5,
          unit: "interactions/week",
          scarcity: 0.6,
          aliases: ["manager", "boss", "supervisor"],
        },
        {
          id: "equipment-laptop",
          name: "laptop",
          category: "equipment",
          capacity: 1,
          unit: "device",
          scarcity: 0.4,
          aliases: ["laptop", "computer", "machine"],
        },
      ],
    });
  }
}
