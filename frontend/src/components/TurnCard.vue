<script setup>
import { computed, ref, watch } from "vue";
import { ensureArray, formatNarrative, formatUserInput } from "../utils/format";
import NarrativeRenderer from "./NarrativeRenderer.vue";

const props = defineProps({
  turn: {
    type: Object,
    required: true,
  },
  openByDefault: {
    type: Boolean,
    default: false,
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

const emit = defineEmits(["pick-option"]);
const isOpen = ref(props.openByDefault || props.turn.is_streaming);

watch(
  () => [props.openByDefault, props.turn.is_streaming],
  ([openByDefault, streaming]) => {
    if (openByDefault || streaming) {
      isOpen.value = true;
    }
  }
);

const userText = computed(() => formatUserInput(props.turn.user_input) || "系统推进了一轮剧情。");
const narrativeText = computed(() => {
  return (
    formatNarrative(props.turn.director_result || {}) ||
    props.turn.stream?.visibleNarrative ||
    (props.turn.is_streaming ? "故事正在展开..." : "本轮没有可见叙事。")
  );
});

const previewText = computed(() => {
  const compact = stripNarrativeTags(narrativeText.value).replace(/\s+/g, " ").trim();
  if (!compact) {
    return "展开查看本轮详情";
  }
  return compact.length > 72 ? `${compact.slice(0, 72)}...` : compact;
});

const plan = computed(() => props.turn.plan || {});
const planPlayerInput = computed(() => props.turn.plan?.player_input || {});
const interactionOptions = computed(() => ensureArray(props.turn.director_result?.interaction?.options));
const goalResolution = computed(() => props.turn.director_result?.goal_resolution || {});
const completedGoals = computed(() => ensureArray(goalResolution.value.completed_goals));
const availableGoals = computed(() => ensureArray(goalResolution.value.available_goals));
const ending = computed(() => props.turn.director_result?.ending || {});
const completedCheckpoints = computed(() => {
  return ensureArray(props.turn.director_result?.goal_update?.checkpoints).filter(
    (checkpoint) => checkpoint?.status === "completed"
  );
});

const characterNameMap = computed(() => {
  const names = Object.fromEntries(
    Object.entries(props.state?.characters || {}).map(([id, item]) => [
      id,
      item?.state?.name || id,
    ])
  );
  for (const character of ensureArray(props.turn.plan?.characters)) {
    if (character.id && !names[character.id] && character.name) {
      names[character.id] = character.name;
    }
  }
  return names;
});

const narrativePayload = computed(() => {
  return props.turn.director_result?.narrative || {
    visible: narrativeText.value,
  };
});

const sceneLabel = computed(() => {
  const scene = props.turn.director_result?.scene;
  if (typeof scene === "string") {
    return locationName(scene);
  }
  if (scene && typeof scene === "object") {
    return scene.name || locationName(scene.id) || "";
  }
  return "";
});

const timeLabel = computed(() => props.turn.director_result?.time || "");

const executionCards = computed(() => {
  const feedbackById = new Map(
    ensureArray(props.turn.env_feedback?.character_feedback).map((item) => [item.character_id, item])
  );
  const streamingById = new Map(
    ensureArray(props.turn.stream?.character_feedback).map((item) => [item.character_id, item])
  );

  return ensureArray(props.turn.plan?.characters).map((character, index) => {
    const doneFeedback = feedbackById.get(character.id);
    const streamingFeedback = streamingById.get(character.id);
    const feedback = doneFeedback || streamingFeedback || null;
    const response = feedback?.response || "";
    const rawText = feedback?.raw_text || "";
    const order = safeOrder(character.order, index + 1);
    const name = characterName(character.id, character.name);

    return {
      id: character.id || `character_${index + 1}`,
      name,
      order,
      response,
      rawText,
      emotion: feedback?.emotion || "",
      stateUpdate: feedback?.state_update || {},
      updateRows: buildUpdateRows(feedback?.state_update || {}),
      isResponding: props.turn.active_stage === "responding" && !!feedback?.streaming && !doneFeedback,
      isPlanned:
        props.turn.active_stage === "planning" &&
        !response &&
        !doneFeedback &&
        !streamingFeedback &&
        index === ensureArray(props.turn.plan?.characters).length - 1,
    };
  });
});

const executionRows = computed(() => {
  const groups = new Map();
  for (const card of executionCards.value) {
    if (!groups.has(card.order)) {
      groups.set(card.order, []);
    }
    groups.get(card.order).push(card);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left - right)
    .map(([order, cards]) => ({ order, cards }));
});

const characterOutcomeCards = computed(() => {
  return executionCards.value.filter((card) => card.emotion || card.updateRows.length);
});

const directorUpdateGroups = computed(() => {
  const update = props.turn.director_result?.state_update || {};
  const groups = [
    {
      label: "玩家",
      rows: buildUpdateRows(update.user_state || {}),
    },
    {
      label: "世界",
      rows: buildUpdateRows(update.world_state || {}),
    },
  ];
  return groups.filter((group) => group.rows.length);
});

const goalNoticeItems = computed(() => {
  const items = [];
  for (const checkpoint of completedCheckpoints.value) {
    items.push({
      key: `checkpoint-${checkpoint.goal_id}-${checkpoint.checkpoint_id}`,
      text: `进度完成：${checkpointLabel(checkpoint.goal_id, checkpoint.checkpoint_id)}`,
    });
  }
  for (const goalId of completedGoals.value) {
    items.push({
      key: `completed-${goalId}`,
      text: `目标完成：${goalName(goalId)}`,
    });
  }
  for (const goalId of availableGoals.value) {
    items.push({
      key: `available-${goalId}`,
      text: `新目标可选：${goalName(goalId)}`,
    });
  }
  if (ending.value?.is_ended) {
    items.unshift({
      key: "ending",
      text: `结局达成：${ending.value.title || "终局"}`,
      ending: true,
    });
  }
  return items;
});

const hasOutcome = computed(() => {
  return characterOutcomeCards.value.length || directorUpdateGroups.value.length || goalNoticeItems.value.length;
});

function safeOrder(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stripNarrativeTags(text) {
  return String(text || "")
    .replace(/<\/?scene\b[^>]*>/gi, "")
    .replace(/<\/?character_response\b[^>]*>/gi, "")
    .replace(/<\/?[^>]+>/g, "");
}

function onPickOption(option) {
  if (props.interactive) {
    emit("pick-option", option);
  }
}

function characterName(characterId, fallback = "") {
  return characterNameMap.value?.[characterId] || fallback || "角色";
}

function locationName(locationId) {
  if (!locationId) {
    return "";
  }
  const location = props.state?.world_state?.map_locations?.[locationId];
  return location?.name || locationId;
}

function goalName(goalId) {
  return props.state?.goals?.definitions?.[goalId]?.title || goalId;
}

function checkpointLabel(goalId, checkpointId) {
  const checkpoints = props.state?.goals?.definitions?.[goalId]?.checkpoints || [];
  const checkpoint = checkpoints.find((item) => item.id === checkpointId);
  return checkpoint?.description || checkpoint?.title || checkpointId;
}

function normalizeDeltaPath(path) {
  const statRules = props.state?.stat_rules || props.state?.config?.stat_rules || {};
  if (!path.includes(".") && statRules?.[path]) {
    return `stats.${path}.value`;
  }
  if (path.startsWith("relations.")) {
    const segments = path.split(".");
    if (segments.length === 3) {
      return `${path}.value`;
    }
  }
  if (path.startsWith("stats.") && !path.endsWith(".value")) {
    const segments = path.split(".");
    if (segments.length === 2) {
      return `${path}.value`;
    }
  }
  return path;
}

function flattenDelta(delta, prefix = "") {
  const rows = [];
  Object.entries(delta || {}).forEach(([key, value]) => {
    if (value && typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length) {
      return;
    }
    const rawPath = prefix ? `${prefix}.${key}` : key;
    const path = normalizeDeltaPath(rawPath);
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      !("value" in value || "description" in value || "change" in value)
    ) {
      rows.push(...flattenDelta(value, path));
    } else {
      rows.push({ path, value });
    }
  });
  return rows;
}

function formatValue(value, path = "") {
  if (value === undefined || value === null || value === "") {
    return "未知";
  }
  if (path === "location") {
    return locationName(value);
  }
  if (Array.isArray(value)) {
    return value.join("、");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function splitChangeText(value) {
  if (typeof value !== "string") {
    return { before: undefined, after: value };
  }
  const separator = value.includes("->") ? "->" : value.includes("→") ? "→" : "";
  if (!separator) {
    return { before: undefined, after: value };
  }
  const [before, ...rest] = value.split(separator);
  return {
    before: before.trim(),
    after: rest.join(separator).trim(),
  };
}

function statDisplayName(statName) {
  return (
    props.statRules?.[statName]?.display_name ||
    props.statRules?.character_state?.[statName]?.display_name ||
    props.statRules?.user_state?.[statName]?.display_name ||
    props.state?.stat_rules?.[statName]?.display_name ||
    props.state?.config?.stat_rules?.[statName]?.display_name ||
    statName
  );
}

function relationDisplayName(relationName) {
  return (
    props.state?.relation_rules?.[relationName]?.display_name ||
    props.state?.config?.relation_rules?.[relationName]?.display_name ||
    (relationName === "player" || relationName === "user" ? "对玩家关系" : relationName || "关系值")
  );
}

function pathDisplayLabel(path) {
  const normalizedPath = normalizeDeltaPath(path);
  if (normalizedPath === "emotion") {
    return "情绪";
  }
  if (normalizedPath === "location") {
    return "地点";
  }
  if (normalizedPath === "time") {
    return "时间";
  }
  if (normalizedPath === "weather") {
    return "天气";
  }
  if (normalizedPath === "inventory" || normalizedPath === "possessions") {
    return "物品";
  }
  if (normalizedPath.startsWith("relations.")) {
    const segments = normalizedPath.split(".");
    const metric = segments.length >= 4 ? segments[2] : segments[1] || "";
    return relationDisplayName(metric);
  }
  const statMatch = normalizedPath.match(/^stats\.([^.]+)\.value$/);
  if (statMatch) {
    return statDisplayName(statMatch[1]);
  }
  return normalizedPath;
}

function buildUpdateRows(stateUpdate) {
  return flattenDelta(stateUpdate).map((row) => {
    const normalizedPath = normalizeDeltaPath(row.path);
    const isObjectValue = row.value && typeof row.value === "object";
    const rawChange = isObjectValue && "change" in row.value ? row.value.change : null;
    const after = isObjectValue && "value" in row.value ? row.value.value : row.value;
    const change = splitChangeText(rawChange || after);
    return {
      label: pathDisplayLabel(normalizedPath),
      path: normalizedPath,
      before: formatValue(change.before, normalizedPath),
      after: formatValue(change.after, normalizedPath),
      reason: isObjectValue ? row.value.reason || "" : "",
    };
  });
}

</script>

<template>
  <article class="turn-card chat-turn" :class="{ 'turn-card-streaming': turn.is_streaming }">
    <div class="turn-kicker chat-turn-kicker">
      Turn {{ turn.turn_index || "?" }}<span v-if="turn.is_streaming"> · Live</span>
    </div>

    <div class="chat-message-row user-message-row">
      <div class="chat-bubble user-bubble">
        <div class="bubble-label">你</div>
        <div class="message-content">{{ userText }}</div>
      </div>
    </div>

    <details
      v-if="planPlayerInput.intent || planPlayerInput.action || planPlayerInput.speech || plan.context || executionCards.length"
      class="plan-detail"
    >
      <summary>
        <span>本轮计划</span>
        <span>{{ executionCards.length ? `${executionCards.length} 位角色` : "无角色回应" }}</span>
      </summary>
      <div class="plan-detail-body">
        <p v-if="planPlayerInput.intent"><strong>意图：</strong>{{ planPlayerInput.intent }}</p>
        <p v-if="planPlayerInput.action"><strong>行动：</strong>{{ planPlayerInput.action }}</p>
        <p v-if="planPlayerInput.speech"><strong>对白：</strong>{{ planPlayerInput.speech }}</p>
        <p v-if="plan.context"><strong>上下文：</strong>{{ plan.context }}</p>
      </div>
    </details>

    <div v-if="executionRows.length" class="character-order-stack">
      <div v-for="row in executionRows" :key="row.order" class="character-order-row">
        <article
          v-for="card in row.cards"
          :key="card.id"
          class="character-mini-bubble"
          :class="{ 'is-active': card.isPlanned || card.isResponding }"
        >
          <div class="mini-bubble-header">
            <strong>{{ card.name }}</strong>
            <span v-if="card.isResponding" class="live-badge">正在回应...</span>
            <span v-else-if="card.isPlanned" class="live-badge">准备回应...</span>
          </div>
          <div class="mini-bubble-text">
            {{
              card.response ||
              card.rawText ||
              (card.isPlanned || card.isResponding ? `${card.name} 正在回应...` : "")
            }}
          </div>
        </article>
      </div>
    </div>

    <div v-else-if="turn.is_streaming" class="character-order-stack">
      <div class="character-order-row">
        <article class="character-mini-bubble is-active">
          <div class="mini-bubble-header">
            <strong>角色</strong>
            <span class="live-badge">正在判断是否回应...</span>
          </div>
        </article>
      </div>
    </div>

    <div class="chat-message-row assistant-message-row">
      <div class="chat-bubble assistant-bubble" :class="{ 'is-active': turn.active_stage === 'narrating' }">
        <div class="bubble-label">
          叙事
          <span v-if="turn.active_stage === 'narrating'" class="live-badge">正在叙述...</span>
        </div>
        <div v-if="timeLabel || sceneLabel" class="turn-scene-meta">
          <div v-if="timeLabel" class="turn-scene-line">📅 <strong>时间：</strong>{{ timeLabel }}</div>
          <div v-if="sceneLabel" class="turn-scene-line">🏘️ <strong>场所：</strong>{{ sceneLabel }}</div>
        </div>
        <NarrativeRenderer
          :narrative="narrativePayload"
          :fallback-text="narrativeText"
        />
      </div>
    </div>

    <section v-if="hasOutcome" class="turn-outcome-panel">
      <div class="outcome-title">本轮结果</div>

      <div v-if="characterOutcomeCards.length" class="outcome-group-list">
        <article v-for="card in characterOutcomeCards" :key="`outcome-${card.id}`" class="outcome-group">
          <strong>{{ card.name }}</strong>
          <div class="state-change-list">
            <span v-if="card.emotion" class="state-chip">情绪 {{ card.emotion }}</span>
            <div
              v-for="row in card.updateRows"
              :key="`${card.id}-${row.label}-${row.before}-${row.after}`"
              class="state-change-line"
              :title="row.reason"
            >
              <span class="state-change-label">[{{ row.label }}]</span>
              <span v-if="row.before && row.before !== '未知'" class="state-change-value">
                {{ row.before }} <span class="state-change-arrow">→</span> {{ row.after }}
              </span>
              <span v-else class="state-change-value">{{ row.after }}</span>
              <small v-if="row.reason">{{ row.reason }}</small>
            </div>
          </div>
        </article>
      </div>

      <div v-if="directorUpdateGroups.length" class="outcome-group-list">
        <article v-for="group in directorUpdateGroups" :key="group.label" class="outcome-group">
          <strong>{{ group.label }}</strong>
          <div class="state-change-list">
            <div
              v-for="row in group.rows"
              :key="`${group.label}-${row.label}-${row.before}-${row.after}`"
              class="state-change-line"
              :title="row.reason"
            >
              <span class="state-change-label">[{{ row.label }}]</span>
              <span v-if="row.before && row.before !== '未知'" class="state-change-value">
                {{ row.before }} <span class="state-change-arrow">→</span> {{ row.after }}
              </span>
              <span v-else class="state-change-value">{{ row.after }}</span>
              <small v-if="row.reason">{{ row.reason }}</small>
            </div>
          </div>
        </article>
      </div>

      <div v-if="goalNoticeItems.length" class="turn-meta-strip">
        <span
          v-for="item in goalNoticeItems"
          :key="item.key"
          :class="{ 'completion-badge': item.ending }"
        >
          {{ item.text }}
        </span>
      </div>
    </section>

    <section v-if="interactionOptions.length" class="option-section chat-option-section">
      <div class="option-title">本轮建议</div>
      <div class="option-list">
        <div
          v-for="(option, index) in interactionOptions"
          :key="option.id || option.text"
          class="option-line"
          :class="{ 'option-line-interactive': interactive }"
          @click="onPickOption(option)"
        >
          <span class="option-id">{{ index + 1 }}</span>
          <span class="option-text">{{ option.text }}</span>
        </div>
      </div>
    </section>
  </article>
</template>
