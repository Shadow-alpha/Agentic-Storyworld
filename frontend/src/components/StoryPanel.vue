<script setup>
import { computed } from "vue";

const props = defineProps({
  story: {
    type: Object,
    default: () => ({}),
  },
});

const modeLabel = computed(() => {
  const labels = {
    none: "自由推进",
    time_skip: "时间流逝",
    pressure: "事件逼近",
    closure: "事件收束",
  };
  return labels[props.story.mode] || props.story.mode || "自由推进";
});

const currentId = computed(() => props.story.current || "");

function nodeStatusLabel(status) {
  return {
    completed: "已完成",
    current: "当前",
    upcoming: "未发生",
  }[status] || status;
}

function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined || minutes === "") {
    return "";
  }
  const value = Number(minutes);
  if (!Number.isFinite(value)) {
    return "";
  }
  if (value >= 1440) {
    return `${Math.floor(value / 1440)} 天 ${value % 1440} 分钟`;
  }
  if (value >= 60) {
    return `${Math.floor(value / 60)} 小时 ${value % 60} 分钟`;
  }
  return `${value} 分钟`;
}

function isCurrentNode(node) {
  return node?.id === currentId.value || node?.status === "current";
}
</script>

<template>
  <section class="goal-banner story-panel">
    <div class="goal-banner-body">
      <article v-if="story.nodes?.length" class="story-sequence-card">
        <div
          v-for="node in story.nodes"
          :key="node.id"
          class="story-node-row"
          :class="`story-node-${node.status}`"
        >
          <span class="story-node-dot"></span>
          <div class="story-node-content">
            <div class="story-node-title-line">
              <strong>{{ node.title || node.id }}</strong>
              <small>{{ nodeStatusLabel(node.status) }}</small>
            </div>

            <div v-if="isCurrentNode(node)" class="story-node-detail">
              <div class="goal-card-title-row">
                <span class="goal-progress-pill compact-progress">{{ story.status || "unstarted" }}</span>
                <span class="goal-progress-pill compact-progress story-mode-pill">{{ modeLabel }}</span>
              </div>

              <p v-if="story.description">{{ story.description }}</p>

              <div class="checkpoint-track">
                <span v-if="story.scene" class="checkpoint-pill">
                  <span class="checkpoint-dot"></span>
                  <span>关联场景：{{ story.scene }}</span>
                </span>
                <template v-if="story.status === 'unstarted'">
                  <span v-if="story.pace?.start_at" class="checkpoint-pill">
                    <span class="checkpoint-dot"></span>
                    <span>计划开始：{{ story.pace.start_at }}</span>
                  </span>
                  <span v-if="story.turns_until_start !== null && story.turns_until_start !== undefined" class="checkpoint-pill">
                    <span class="checkpoint-dot"></span>
                    <span>自由回合剩余：{{ story.turns_until_start }} 回合</span>
                  </span>
                  <span v-if="formatMinutes(story.minutes_until_start)" class="checkpoint-pill">
                    <span class="checkpoint-dot"></span>
                    <span>距离开始：{{ formatMinutes(story.minutes_until_start) }}</span>
                  </span>
                </template>
                <template v-else>
                  <span class="checkpoint-pill">
                    <span class="checkpoint-dot"></span>
                    <span>推进回合：{{ story.turns_since_started || 0 }} / {{ story.pace?.soft_turns || "-" }}</span>
                  </span>
                  <span class="checkpoint-pill">
                    <span class="checkpoint-dot"></span>
                    <span>推进时间：{{ story.elapsed_minutes_since_started || 0 }} / {{ story.pace?.soft_duration || "-" }} 分钟</span>
                  </span>
                </template>
              </div>

              <p v-if="story.event_progress" class="goal-description">{{ story.event_progress }}</p>
              <p v-else-if="story.push" class="goal-description">{{ story.push }}</p>
            </div>
          </div>
        </div>
      </article>

      <article v-if="story.ending_state?.is_ended" class="goal-choice-card completed-goal-card">
        <strong>{{ story.ending_state.title || "结局达成" }}</strong>
        <p>{{ story.ending_state.narrative || story.ending_state.description }}</p>
      </article>
    </div>
  </section>
</template>
