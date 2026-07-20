<script setup>
import { computed } from "vue";

const props = defineProps({
  narrative: {
    type: [Object, String],
    default: () => ({}),
  },
  fallbackText: {
    type: String,
    default: "",
  },
});

const plainText = computed(() => {
  if (typeof props.narrative === "string") {
    return props.narrative;
  }
  return props.narrative?.visible || props.fallbackText || "";
});

const parts = computed(() => parseNarrativeParts(plainText.value));

function parseNarrativeParts(text) {
  const source = String(text || "");
  const parts = [];
  const pattern = /<character_response\b([^>]*)>([\s\S]*?)<\/character_response>/gi;
  let cursor = 0;
  let match = null;

  while ((match = pattern.exec(source)) !== null) {
    pushNarration(parts, source.slice(cursor, match.index));
    const responseText = cleanNarrativeText(match[2]);
    if (responseText.trim()) {
      parts.push({
        type: "character_response",
        character_id: extractAttr(match[1] || "", "id"),
        text: responseText,
      });
    }
    cursor = pattern.lastIndex;
  }

  pushNarration(parts, source.slice(cursor));
  return parts.length ? parts : [{ type: "scene", text: cleanNarrativeText(source) }];
}

function pushNarration(parts, text) {
  const cleaned = cleanNarrativeText(text);
  if (cleaned.trim()) {
    parts.push({ type: "scene", text: cleaned });
  }
}

function cleanNarrativeText(text) {
  return String(text || "")
    .replace(/<\/?scene\b[^>]*>/gi, "")
    .replace(/<character_response\b[^>]*>[\s\S]*$/i, "")
    .replace(/<[^>\n]*$/g, "")
    .replace(/<\/?[^>]+>/g, "");
}

function extractAttr(attrs, name) {
  const pattern = new RegExp(`${name}\\s*=\\s*["'‘’“”]([^"'‘’“”]*)["'‘’“”]`, "i");
  return attrs.match(pattern)?.[1] || "";
}
</script>

<template>
  <div class="narrative-renderer">
    <span
      v-for="(segment, index) in parts"
      :key="`${segment.type || 'segment'}-${segment.character_id || index}-${index}`"
      class="narrative-segment"
      :class="`narrative-segment-${segment.type || 'scene'}`"
    >
      <span>{{ segment.text }}</span>
    </span>
  </div>
</template>
