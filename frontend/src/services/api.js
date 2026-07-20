function buildApiUrl(path, gameId = null) {
  const url = new URL(path, window.location.origin);
  if (gameId) {
    url.searchParams.set("game_id", gameId);
  }
  return url.toString();
}

async function parseJsonError(response) {
  const errorPayload = await response.json().catch(() => ({}));
  throw new Error(errorPayload.detail || errorPayload.message || `Request failed: ${response.status}`);
}

export async function apiGet(path, gameId = null) {
  const response = await fetch(buildApiUrl(path, gameId));
  if (!response.ok) {
    await parseJsonError(response);
  }
  return response.json();
}

export async function apiPost(path, body = {}, gameId = null) {
  const response = await fetch(buildApiUrl(path, gameId), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await parseJsonError(response);
  }
  return response.json();
}
