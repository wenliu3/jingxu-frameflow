// Bound background reads while preserving the source order of their results.
export async function mapConcurrent(items, mapper, { concurrency = 6, isActive = () => true } = {}) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError('concurrency must be a positive integer');
  }
  const results = Array(items.length).fill(null);
  let next = 0;
  let failed = false;
  const worker = async () => {
    while (!failed && isActive() && next < items.length) {
      const index = next++;
      try {
        results[index] = await mapper(items[index], index);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
  return results;
}
