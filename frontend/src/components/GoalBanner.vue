<script setup>
import { computed } from "vue";

const props = defineProps({
  goalsConfig: {
    type: Object,
    default: () => ({}),
  },
  streamingTurn: {
    type: Object,
    default: null,
  },
});

const emit = defineEmits(["activate-goal", "deactivate-goal"]);

const goals = computed(() => props.goalsConfig?.definitions || {});
const activeGoalProgress = computed(() => props.goalsConfig?.active_goals || {});
const availableGoalProgress = computed(() => props.goalsConfig?.available_goals || {});
const completedGoalIds = computed(() => props.goalsConfig?.completed_goals || []);
const endingState = computed(() => props.goalsConfig?.ending_state || {});

function buildGoalItems(goalProgress, completed = false) {
  const entries = Array.isArray(goalProgress)
    ? goalProgress.map((goalId) => [goalId, {}])
    : Object.entries(goalProgress || {});
  return entries
    .map(([goalId, checkpointProgress]) => {
      const goal = goals.value[goalId];
      if (!goal) {
        return null;
      }
      const checkpoints = (goal.checkpoints || []).map((checkpoint) => ({
        ...checkpoint,
        ...(checkpointProgress?.[checkpoint.id] || {}),
        status: completed ? "completed" : checkpointProgress?.[checkpoint.id]?.status || checkpoint.status,
        is_completed: completed || checkpointProgress?.[checkpoint.id]?.status === "completed",
      }));
      const doneCount = checkpoints.filter((item) => item.is_completed).length;
      return {
        id: goalId,
        ...goal,
        checkpoints,
        doneCount,
        totalCount: checkpoints.length,
      };
    })
    .filter(Boolean);
}

const activeGoals = computed(() => buildGoalItems(activeGoalProgress.value));
const availableGoals = computed(() => buildGoalItems(availableGoalProgress.value));
const completedGoals = computed(() => buildGoalItems(completedGoalIds.value, true));

const latestStreamingCheckpoints = computed(() => {
  const goalUpdate = props.streamingTurn?.director_result?.goal_update || {};
  return goalUpdate.checkpoints || [];
});
</script>

<template>
  <section class="goal-banner" :class="{ 'goal-banner-ended': endingState.is_ended }">
    <div class="goal-banner-header">
      <div>
        <p class="eyebrow">Objectives</p>
      </div>
      <div v-if="endingState.is_ended" class="goal-progress-pill ending-pill">Ending</div>
    </div>

    <div class="goal-banner-body">
      <div v-if="latestStreamingCheckpoints.length" class="goal-flash">
        本轮已识别目标进展：{{ latestStreamingCheckpoints.map((item) => item.checkpoint_id).join("、") }}
      </div>

      <article v-if="endingState.is_ended" class="goal-choice-card active-goal-card goal-card-stack">
        <strong>{{ endingState.title || "已达成结局" }}</strong>
        <p>{{ endingState.narrative || endingState.description }}</p>
        <p v-if="endingState.narrative && endingState.description" class="goal-description">
          {{ endingState.description }}
        </p>
      </article>

      <details v-if="activeGoals.length && !endingState.is_ended" class="goal-dropdown" open>
        <summary>
          <span>当前目标</span>
          <span>{{ activeGoals.length }} 个</span>
        </summary>
        <div class="goal-card-stack">
          <article v-for="goal in activeGoals" :key="goal.id" class="goal-choice-card active-goal-card">
            <div class="goal-choice-header">
              <div>
                <div class="goal-card-title-row">
                  <strong>{{ goal.title || goal.id }}</strong>
                  <span class="goal-progress-pill compact-progress">
                    {{ goal.doneCount }}/{{ goal.totalCount }}
                  </span>
                </div>
                <p>{{ goal.description }}</p>
              </div>
              <button type="button" class="subtle-button" @click.stop="emit('deactivate-goal', goal.id)">
                退回可选
              </button>
            </div>
            <div class="checkpoint-track">
              <div
                v-for="checkpoint in goal.checkpoints"
                :key="checkpoint.id"
                class="checkpoint-pill"
                :class="{ completed: checkpoint.is_completed }"
              >
                <span class="checkpoint-dot"></span>
                <span>{{ checkpoint.description || checkpoint.id }}</span>
              </div>
            </div>
          </article>
        </div>
      </details>

      <details v-if="availableGoals.length && !endingState.is_ended" class="goal-dropdown" :open="!activeGoals.length">
        <summary>
          <span>可选择目标</span>
          <span>{{ availableGoals.length }} 个</span>
        </summary>
        <div class="goal-card-stack">
          <article v-for="goal in availableGoals" :key="goal.id" class="goal-choice-card">
            <div>
              <div class="goal-card-title-row">
                <strong>{{ goal.title || goal.id }}</strong>
                <span class="goal-progress-pill compact-progress">
                  {{ goal.doneCount }}/{{ goal.totalCount }}
                </span>
              </div>
              <p>{{ goal.description }}</p>
            </div>
            <button type="button" @click.stop="emit('activate-goal', goal.id)">设为当前目标</button>
          </article>
        </div>
      </details>

      <details v-if="completedGoals.length" class="goal-dropdown compact-goal-dropdown">
        <summary>
          <span>已完成目标</span>
          <span>{{ completedGoals.length }} 个</span>
        </summary>
        <div class="goal-card-stack">
          <article v-for="goal in completedGoals" :key="goal.id" class="goal-choice-card completed-goal-card">
            <div>
              <strong>{{ goal.title || goal.id }}</strong>
              <p>{{ goal.description }}</p>
            </div>
            <span class="completed-check">完成</span>
          </article>
        </div>
      </details>
    </div>
  </section>
</template>
