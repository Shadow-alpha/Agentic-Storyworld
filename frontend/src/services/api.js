function buildApiUrl(path, gameId = null) {
  const url = new URL(path, window.location.origin);
  if (gameId) {
    url.searchParams.set("game_id", gameId);
  }
  return url.toString();
}

const ACCESS_TOKEN_KEY = "access_token";

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY) || "";
}

export function setAccessToken(token) {
  if (token) {
    localStorage.setItem(ACCESS_TOKEN_KEY, token);
  } else {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
  }
}

export function authHeaders() {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseJsonError(response) {
  const errorPayload = await response.json().catch(() => ({}));
  const error = new Error(errorPayload.detail || errorPayload.message || `Request failed: ${response.status}`);
  error.status = response.status;
  throw error;
}

export async function apiGet(path, gameId = null) {
  const response = await fetch(buildApiUrl(path, gameId), {
    headers: authHeaders(),
  });
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
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await parseJsonError(response);
  }
  return response.json();
}
