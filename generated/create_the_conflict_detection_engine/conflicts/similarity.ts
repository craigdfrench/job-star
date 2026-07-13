/**
 * src/conflicts/similarity.ts
 *
 * Utility functions for text similarity comparison used by conflict detectors.
 * Supports:
 *   - Token-based overlap (Jaccard, Dice)
 *   - N-gram similarity
 *   - Embedding cosine similarity (when embeddings are available)
 *
 * Part of Job-Star's conflict detection engine.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SimilarityOptions {
  /** Normalize text before comparison (lowercase, strip punctuation). Default: true */
  normalize?: boolean;
  /** Remove stop words before token comparison. Default: true */
  removeStopWords?: boolean;
  /** N-gram size for n-gram similarity. Default: 2 */
  ngramSize?: number;
}

export interface EmbeddingVector {
  values: number[];
  model?: string;
}

// ---------------------------------------------------------------------------
// Stop words (minimal set — extend as needed)
// ---------------------------------------------------------------------------

const STOP_WORDS = new Set<string>([
  'a', 'an', 'the', 'and', 'or', 'but', 'if', 'then', 'else', 'when',
  'at', 'by', 'for', 'with', 'about', 'against', 'between', 'into',
  'through', 'during', 'before', 'after', 'above', 'below', 'to', 'from',
  'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under', 'again',
  'further', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have',
  'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should',
  'may', 'might', 'must', 'shall', 'can', 'need', 'of', 'as', 'this',
  'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
  'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his', 'its', 'our',
  'their', 'goal', 'want', 'need', 'get', 'make',
]);

// ---------------------------------------------------------------------------
// Text normalization
// ---------------------------------------------------------------------------

/**
 * Normalize text: lowercase, strip extra whitespace and punctuation.
 */
export function normalizeText(text: string): string {
  if (!text) return '';
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Tokenize text into words, optionally removing stop words.
 */
export function tokenize(
  text: string,
  options: SimilarityOptions = {},
): string[] {
  const { normalize = true, removeStopWords = true } = options;
  const cleaned = normalize ? normalizeText(text) : text;
  const tokens = cleaned.split(/\s+/).filter(Boolean);
  return removeStopWords ? tokens.filter((t) => !STOP_WORDS.has(t)) : tokens;
}

// ---------------------------------------------------------------------------
// Set-based similarity
// ---------------------------------------------------------------------------

/**
 * Jaccard similarity between two token sets: |A ∩ B| / |A ∪ B|.
 * Returns 0..1.
 */
export function jaccardSimilarity(
  textA: string,
  textB: string,
  options: SimilarityOptions = {},
): number {
  const tokensA = new Set(tokenize(textA, options));
  const tokensB = new Set(tokenize(textB, options));
  if (tokensA.size === 0 && tokensB.size === 0) return 1.0;
  if (tokensA.size === 0 || tokensB.size === 0) return 0.0;

  let intersection = 0;
  for (const t of tokensA) {
    if (tokensB.has(t)) intersection++;
  }
  const union = tokensA.size + tokensB.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

/**
 * Dice coefficient (Sørensen–Dice): 2|A ∩ B| / (|A| + |B|).
 * Returns 0..1. Slightly favors partial overlaps more than Jaccard.
 */
export function diceSimilarity(
  textA: string,
  textB: string,
  options: SimilarityOptions = {},
): number {
  const tokensA = new Set(tokenize(textA, options));
  const tokensB = new Set(tokenize(textB, options));
  if (tokensA.size === 0 && tokensB.size === 0) return 1.0;
  if (tokensA.size === 0 || tokensB.size === 0) return 0.0;

  let intersection = 0;
  for (const t of tokensA) {
    if (tokensB.has(t)) intersection++;
  }
  return (2 * intersection) / (tokensA.size + tokensB.size);
}

// ---------------------------------------------------------------------------
// N-gram similarity
// ---------------------------------------------------------------------------

/**
 * Generate character n-grams from text.
 */
export function generateNgrams(text: string, n: number = 2): Set<string> {
  const cleaned = normalizeText(text).replace(/\s+/g, ' ');
  if (cleaned.length < n) return new Set([cleaned]);
  const ngrams = new Set<string>();
  for (let i = 0; i <= cleaned.length - n; i++) {
    ngrams.add(cleaned.slice(i, i + n));
  }
  return ngrams;
}

/**
 * N-gram Jaccard similarity — useful for catching typos and near-identical phrasing.
 */
export function ngramSimilarity(
  textA: string,
  textB: string,
  n: number = 2,
): number {
  const ngramsA = generateNgrams(textA, n);
  const ngramsB = generateNgrams(textB, n);
  if (ngramsA.size === 0 && ngramsB.size === 0) return 1.0;
  if (ngramsA.size === 0 || ngramsB.size === 0) return 0.0;

  let intersection = 0;
  for (const g of ngramsA) {
    if (ngramsB.has(g)) intersection++;
  }
  const union = ngramsA.size + ngramsB.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

// ---------------------------------------------------------------------------
// Embedding cosine similarity
// ---------------------------------------------------------------------------

/**
 * Compute cosine similarity between two embedding vectors.
 * Returns -1..1, but for typical embeddings 0..1.
 */
export function cosineSimilarity(
  vecA: EmbeddingVector | number[],
  vecB: EmbeddingVector | number[],
): number {
  const a = Array.isArray(vecA) ? vecA : vecA.values;
  const b = Array.isArray(vecB) ? vecB : vecB.values;

  if (a.length !== b.length) {
    throw new Error(
      `Vector dimension mismatch: ${a.length} vs ${b.length}`,
    );
  }
  if (a.length === 0) return 0.0;

  let dot = 0;
  let magA = 0;
  let magB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    magA += a[i] * a[i];
    magB += b[i] * b[i];
  }
  const denom = Math.sqrt(magA) * Math.sqrt(magB);
  return denom === 0 ? 0.0 : dot / denom;
}

// ---------------------------------------------------------------------------
// Combined / composite similarity
// ---------------------------------------------------------------------------

export interface CompositeSimilarityResult {
  /** Overall similarity score 0..1 */
  score: number;
  /** Individual component scores */
  components: {
    jaccard: number;
    dice: number;
    ngram: number;
    embedding?: number;
  };
  /** Whether embeddings were used */
  usedEmbeddings: boolean;
}

export interface CompositeSimilarityOptions extends SimilarityOptions {
  /** Weight for Jaccard token similarity. Default: 0.3 */
  jaccardWeight?: number;
  /** Weight for Dice token similarity. Default: 0.2 */
  diceWeight?: number;
  /** Weight for n-gram similarity. Default: 0.2 */
  ngramWeight?: number;
  /** Weight for embedding cosine similarity. Default: 0.3 */
  embeddingWeight?: number;
  /** Optional embedding for text A */
  embeddingA?: EmbeddingVector;
  /** Optional embedding for text B */
  embeddingB?: EmbeddingVector;
}

/**
 * Compute a weighted composite similarity score combining lexical and semantic signals.
 * If embeddings are provided, they're included; otherwise weight is redistributed.
 */
export function compositeSimilarity(
  textA: string,
  textB: string,
  options: CompositeSimilarityOptions = {},
): CompositeSimilarityResult {
  const {
    jaccardWeight = 0.3,
    diceWeight = 0.2,
    ngramWeight = 0.2,
    embeddingWeight = 0.3,
    embeddingA,
    embeddingB,
    ngramSize = 2,
  } = options;

  const jaccard = jaccardSimilarity(textA, textB, options);
  const dice = diceSimilarity(textA, textB, options);
  const ngram = ngramSimilarity(textA, textB, ngramSize);

  const hasEmbeddings = !!embeddingA && !!embeddingB;
  const embedding = hasEmbeddings
    ? cosineSimilarity(embeddingA!, embeddingB!)
    : undefined;

  // Normalize embedding cosine from [-1,1] to [0,1]
  const embeddingNormalized = embedding !== undefined ? (embedding + 1) / 2 : undefined;

  let totalWeight: number;
  let score: number;

  if (embeddingNormalized !== undefined) {
    totalWeight = jaccardWeight + diceWeight + ngramWeight + embeddingWeight;
    score =
      (jaccard * jaccardWeight +
        dice * diceWeight +
        ngram * ngramWeight +
        embeddingNormalized * embeddingWeight) /
      totalWeight;
  } else {
    // Redistribute embedding weight proportionally
    totalWeight = jaccardWeight + diceWeight + ngramWeight;
    score =
      (jaccard * jaccardWeight + dice * diceWeight + ngram * ngramWeight) /
      totalWeight;
  }

  return {
    score,
    components: {
      jaccard,
      dice,
      ngram,
      embedding: embeddingNormalized,
    },
    usedEmbeddings: hasEmbeddings,
  };
}
