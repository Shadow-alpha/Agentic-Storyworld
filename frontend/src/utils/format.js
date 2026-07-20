export function formatJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

export function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

export function formatUserInput(userInput) {
  if (!userInput) {
    return "";
  }
  return userInput.raw_text || userInput.selected_choice || "";
}

export function formatNarrative(directorResult) {
  const narrative = directorResult?.narrative;
  if (typeof narrative === "string") {
    return narrative;
  }
  if (narrative && typeof narrative === "object") {
    return narrative.visible || "";
  }
  return "";
}

export function buildUserStatePreview(userState) {
  const fields = [
    userState?.location,
    userState?.stats?.health?.value,
  ].filter((value) => value !== undefined && value !== null && value !== "");
  return fields.join(" · ");
}

export function buildWorldStatePreview(worldState) {
  const locationCount = Object.keys(worldState?.map_locations || {}).length;
  const fields = [
    worldState?.time,
    worldState?.weather,
    locationCount ? `${locationCount} places` : "",
  ].filter((value) => value !== undefined && value !== null && value !== "");
  return fields.join(" · ");
}
