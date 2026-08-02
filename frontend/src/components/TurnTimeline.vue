<script setup>
import { nextTick, ref, watch } from "vue";
import TurnCard from "./TurnCard.vue";

const props = defineProps({
  turns: {
    type: Array,
    default: () => [],
  },
  openingText: {
    type: String,
    default: "等待第一轮输入。",
  },
  interactive: {
    type: Boolean,
    default: false,
  },
  statRules: {
    type: Object,
    default: () => ({}),
  },
  state: {
    type: Object,
    default: () => ({}),
  },
});

defineEmits(["pick-option"]);

const listRef = ref(null);

function scrollToBottom() {
  nextTick(() => {
    requestAnimationFrame(() => {
      const element = listRef.value;
      if (element) {
        element.scrollTop = element.scrollHeight;
      }
    });
  });
}

watch(
  () => [
    props.turns.length,
    props.turns.at(-1)?.is_streaming,
    props.turns.at(-1)?.stream?.visibleNarrative,
    props.turns.at(-1)?.director_result?.narrative?.visible,
  ],
  scrollToBottom,
  { immediate: true }
);
</script>

<template>
  <div ref="listRef" class="message-list">
    <article class="turn-card opening-card">
      <div class="opening-card-header">
        <div class="turn-kicker">Opening</div>
        <h3>故事开场</h3>
      </div>
      <div class="opening-text">{{ openingText }}</div>
    </article>

    <TurnCard
      v-for="(turn, index) in turns"
      :key="`${turn.turn_index || 'opening'}-${index}-${turn.is_streaming ? 'stream' : 'done'}`"
      :turn="turn"
      :open-by-default="index === turns.length - 1 || turn.is_streaming"
      :interactive="interactive && index === turns.length - 1"
      :stat-rules="statRules"
      :state="state"
      @pick-option="$emit('pick-option', $event)"
    />
  </div>
</template>
