/** Minimal server-sent-events reader for POST endpoints (EventSource only supports GET). */
export async function* readEventStream(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: unknown }> {
  if (!response.body) return;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (data.length) {
          try {
            yield { event, data: JSON.parse(data.join("\n")) };
          } catch {
            yield { event, data: data.join("\n") };
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
