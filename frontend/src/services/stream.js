function parseSseEvent(rawEvent) {
  const lines = rawEvent.split("\n");
  let eventName = "message";
  const dataLines = [];

  lines.forEach((line) => {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  });

  const payloadText = dataLines.join("\n");
  const data = payloadText ? JSON.parse(payloadText) : {};
  if (eventName === "error") {
    throw new Error(data.detail || "Streaming failed.");
  }
  return { eventName, data };
}

export async function consumeSseResponse(response, onEvent) {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      if (rawEvent.trim()) {
        const { eventName, data } = parseSseEvent(rawEvent);
        onEvent(eventName, data);
      }
      separatorIndex = buffer.indexOf("\n\n");
    }
  }
}
