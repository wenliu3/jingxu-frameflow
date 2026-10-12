// Preserve clicks and editing keys in order while compacting unsent input bursts.
export function createMuseInputQueue(apply, { afterInput, onIdle, onError } = {}) {
  const queue = [];
  let running = null,
    timer,
    stopped = false;

  function start() {
    clearTimeout(timer);
    timer = undefined;
    if (running || stopped || !queue.length) return;
    running = Promise.resolve()
      .then(async () => {
        while (!stopped && queue.length) {
          const input = queue.shift();
          try {
            await apply(input);
          } catch (error) {
            if (!stopped) onError?.(error);
          }
          if (!stopped) await afterInput?.();
        }
      })
      .finally(() => {
        running = null;
        if (!stopped) onIdle?.();
      });
  }

  return {
    get pending() {
      return !!running || queue.length > 0;
    },
    enqueue(input) {
      if (stopped) return;
      const last = queue.at(-1);
      if (input.kind === 'text' && last?.kind === 'text' && last.text.length + input.text.length <= 10000) {
        last.text += input.text;
      } else if (
        input.kind === 'wheel' &&
        last?.kind === 'wheel' &&
        last.x === input.x &&
        last.y === input.y &&
        Math.sign(last.dx) === Math.sign(input.dx) &&
        Math.sign(last.dy) === Math.sign(input.dy) &&
        Math.abs(last.dx + input.dx) <= 2000 &&
        Math.abs(last.dy + input.dy) <= 2000
      ) {
        last.dx += input.dx;
        last.dy += input.dy;
      } else queue.push({ ...input });
      // A brief window batches typing/scroll bursts. Clicks and editing keys
      // flush immediately, including any text that must precede them.
      if (!running) {
        if (input.kind !== 'text' && input.kind !== 'wheel') start();
        else if (!timer) timer = setTimeout(start, 32);
      }
    },
    flush() {
      start();
      return running || Promise.resolve();
    },
    dispose() {
      stopped = true;
      clearTimeout(timer);
      queue.length = 0;
    },
  };
}

export function museFrameInterval({ active, busy, failed } = {}) {
  return failed ? 2000 : busy ? 1500 : active ? 160 : 1000;
}
