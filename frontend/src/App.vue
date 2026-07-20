<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import ControlStrip from "./components/ControlStrip.vue";
import GoalBanner from "./components/GoalBanner.vue";
import PlayerCustomizationForm from "./components/PlayerCustomizationForm.vue";
import StateSidebar from "./components/StateSidebar.vue";
import TurnTimeline from "./components/TurnTimeline.vue";
import { apiGet, apiPost } from "./services/api";
import { consumeSseResponse } from "./services/stream";

const appState = reactive({
  gameId: null,
  availableGames: [],
  interaction: {
    mode: "hybrid",
    options: [],
  },
  saves: [],
  turns: [],
  currentStreamingTurn: null,
  state: {
    user_state: {},
    world_state: {},
    characters: {},
    goals: {},
  },
  selectedCharacterId: null,
});

const connectionStatus = reactive({
  text: "Connecting...",
  healthy: true,
});

const chatInput = ref("");
const saveSlotInput = ref("");
const loadSlotId = ref("");
const selectedGameId = ref("");
const isSending = ref(false);
const customizationCompleted = ref(false);

const timelineTurns = computed(() => {
  const turns = [...appState.turns];
  if (appState.currentStreamingTurn) {
    turns.push(appState.currentStreamingTurn);
  }
  return turns;
});

const gameMeta = computed(() => `Game: ${appState.gameId || "unknown"} · Turns: ${timelineTurns.value.length}`);

const openingText = computed(() => appState.state?.config?.opening || "等待第一轮输入。");
const customizationFields = computed(() => appState.state?.config?.player_customization || {});
const hasCustomizationFields = computed(() => Object.keys(customizationFields.value).length > 0);
const customizationStorageKey = computed(() => `player_customized:${appState.gameId || "unknown"}`);
const needsPlayerCustomization = computed(() => {
  return hasCustomizationFields.value && !timelineTurns.value.length && !customizationCompleted.value;
});

const endingState = computed(() => appState.state?.goals?.ending_state || {});
const isGameEnded = computed(() => !!endingState.value?.is_ended);
const needsGoalChoice = computed(() => {
  const goals = appState.state?.goals || {};
  return !Object.keys(goals.active_goals || {}).length && !!Object.keys(goals.available_goals || {}).length;
});
const inputDisabled = computed(() => {
  return isSending.value || isGameEnded.value || needsGoalChoice.value || needsPlayerCustomization.value;
});

function setConnectionStatus(text, healthy = true) {
  connectionStatus.text = text;
  connectionStatus.healthy = healthy;
}

function createEmptyStreamState() {
  return {
    visibleNarrative: "",
    stateUpdate: null,
    options: [],
    optionsReady: false,
    character_feedback: [],
  };
}

function clonePlain(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

function syncSelectedCharacter() {
  const characterIds = Object.keys(appState.state?.characters || {});
  if (!characterIds.length) {
    appState.selectedCharacterId = null;
    return;
  }
  if (appState.selectedCharacterId && !characterIds.includes(appState.selectedCharacterId)) {
    appState.selectedCharacterId = null;
  }
}

function syncCustomizationFlag() {
  customizationCompleted.value = localStorage.getItem(customizationStorageKey.value) === "true";
}

function createStreamingTurn(turnIndex, userInput) {
  return {
    turn_index: turnIndex,
    timestamp: "",
    user_input: userInput || {},
    plan: { characters: [] },
    env_feedback: { character_feedback: [], env_summary: "" },
    director_result: {
      narrative: { visible: "", hidden: "" },
      goal: {},
      goal_update: { checkpoints: [] },
      goal_resolution: {},
      ending: {},
      interaction: { mode: "hybrid", options: [] },
      state_update: {},
    },
    stream: createEmptyStreamState(),
    is_streaming: true,
    active_stage: "planning",
  };
}

function hydrateFromPayload(payload) {
  appState.gameId = payload.game_id ?? appState.gameId;
  appState.state = payload.state || appState.state;
  appState.interaction = payload.ui?.interaction || {
    mode: payload.ui?.input_mode || "hybrid",
    options: payload.ui?.choices || [],
  };
  appState.turns = payload.ui?.turns || [];
  appState.saves = payload.ui?.saves || appState.saves;
  appState.currentStreamingTurn = null;
  syncSelectedCharacter();
  selectedGameId.value = appState.gameId || "";
  syncCustomizationFlag();
}

function buildApiUrl(path) {
  const url = new URL(path, window.location.origin);
  if (appState.gameId) {
    url.searchParams.set("game_id", appState.gameId);
  }
  return url.toString();
}

function ensureStreamingTurn(turnIndex, userInput) {
  if (!appState.currentStreamingTurn || appState.currentStreamingTurn.turn_index !== turnIndex) {
    appState.currentStreamingTurn = createStreamingTurn(turnIndex, userInput, {
      characters: clonePlain(appState.state.characters),
      user_state: clonePlain(appState.state.user_state),
      world_state: clonePlain(appState.state.world_state),
    });
  }
  return appState.currentStreamingTurn;
}

function ensureStreamingCharacter(turn, characterId) {
  let item = (turn.stream?.character_feedback || []).find((entry) => entry.character_id === characterId);
  if (!item) {
    item = {
      character_id: characterId,
      response: "",
      emotion: "",
      state_update: null,
      memory_append: "",
      streaming: true,
    };
    turn.stream.character_feedback.push(item);
  }
  return item;
}

function upsertPlannedCharacter(turn, character) {
  if (!character?.id) {
    return;
  }
  const planned = Array.isArray(turn.plan?.characters) ? [...turn.plan.characters] : [];
  const existingIndex = planned.findIndex((item) => item?.id === character.id);
  if (existingIndex >= 0) {
    planned[existingIndex] = { ...planned[existingIndex], ...character };
  } else {
    planned.push(character);
  }
  turn.plan.characters = planned;
}

function handleBlockEvent(turn, eventName, data) {
  const stage = data.stage || "";
  const block = data.block || "";

  if (stage === "director_plan" && block === "character") {
    turn.active_stage = eventName === "block_done" ? "responding" : "planning";
    const parsed = data.parsed || {};
    upsertPlannedCharacter(turn, {
      id: parsed.id || data.attrs?.id || "",
      name: parsed.name || parsed.id || data.attrs?.id || "",
      user_intent: parsed.user_intent || "",
      context: parsed.context || "",
      _raw: data.text || "",
    });
    return;
  }

  if (stage === "character") {
    turn.active_stage = "responding";
    const item = ensureStreamingCharacter(turn, data.character_id);
    if (block === "response") {
      item.response = eventName === "block_done" ? data.parsed || data.text || item.response : data.text || item.response;
    } else if (block === "emotion") {
      item.emotion = eventName === "block_done" ? data.parsed || data.text || item.emotion : data.text || item.emotion;
    } else if (eventName === "block_done" && block === "state_update") {
      item.state_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "memory_append") {
      item.memory_append = data.parsed?.text || "";
    }
    return;
  }

  if (stage === "director_narrative") {
    if (block === "narrative") {
      turn.active_stage = "narrating";
      const text = eventName === "block_done" ? data.parsed?.visible || data.text || "" : data.display_text || data.text || "";
      turn.stream.visibleNarrative = text;
      turn.director_result.narrative = { visible: text, hidden: "" };
    } else if (eventName === "block_done" && block === "time") {
      turn.director_result.time = data.parsed || data.text || "";
    } else if (eventName === "block_done" && block === "scene") {
      turn.director_result.scene = data.parsed || data.text || "";
    } else if (eventName === "block_done" && block === "summary") {
      turn.director_result.summary = data.parsed || data.text || "";
    } else if (eventName === "block_done" && block === "movement") {
      turn.director_result.movement = data.parsed || [];
    }
    return;
  }

  if (stage === "director_resolve") {
    turn.active_stage = "resolving";
    if (eventName === "block_done" && block === "goal_update") {
      turn.director_result.goal_update = data.parsed || { checkpoints: [] };
    } else if (eventName === "block_done" && block === "state_update") {
      turn.stream.stateUpdate = data.parsed || {};
      turn.director_result.state_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "interaction") {
      turn.stream.optionsReady = true;
      turn.active_stage = "choices";
      turn.director_result.interaction = data.parsed || { mode: "hybrid", options: [] };
      turn.stream.options = turn.director_result.interaction.options || [];
    }
    return;
  }

  if (stage === "character_reflection") {
    const item = ensureStreamingCharacter(turn, data.character_id);
    if (block === "emotion") {
      item.emotion = eventName === "block_done" ? data.parsed || data.text || item.emotion : data.text || item.emotion;
    } else if (eventName === "block_done" && block === "state_update") {
      item.state_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "memory_append") {
      item.memory_append = data.parsed?.text || data.parsed || "";
    }
  }
}

function handleStageDone(turn, data) {
  if (data.stage === "director_plan") {
    turn.plan = data.plan || turn.plan;
  } else if (data.stage === "character") {
    const feedback = data.character_feedback || {};
    const item = ensureStreamingCharacter(turn, data.character_id || feedback.character_id);
    Object.assign(item, feedback, { streaming: false });
  } else if (data.stage === "environment") {
    turn.env_feedback = data.env_feedback || turn.env_feedback;
  } else if (data.stage === "director_narrative") {
    turn.director_result = { ...turn.director_result, ...(data.narrative_result || {}) };
  } else if (data.stage === "director_resolve") {
    turn.director_result = { ...turn.director_result, ...(data.resolve_result || {}) };
  } else if (data.stage === "character_reflection") {
    const reflection = data.character_reflection || {};
    const item = ensureStreamingCharacter(turn, data.character_id || reflection.character_id);
    Object.assign(item, reflection, { streaming: false });
  }
}

function handleStageStarted(turn, data) {
  if (data.stage === "director_plan") {
    turn.active_stage = "planning";
  } else if (data.stage === "character") {
    turn.active_stage = "responding";
  } else if (data.stage === "director_narrative") {
    turn.active_stage = "narrating";
  } else if (data.stage === "director_resolve" || data.stage === "character_reflection") {
    turn.active_stage = "resolving";
  }
}

function handleStreamEvent(eventName, data) {
  const turn = ensureStreamingTurn(data.turn_index, data.user_input);

  if (eventName === "turn_started") {
    turn.user_input = data.user_input || turn.user_input;
    appState.interaction.options = [];
    turn.active_stage = "planning";
  } else if (eventName === "block_started" || eventName === "block_delta" || eventName === "block_done") {
    handleBlockEvent(turn, eventName, data);
  } else if (eventName === "stage_started") {
    handleStageStarted(turn, data);
  } else if (eventName === "stage_done") {
    handleStageDone(turn, data);
  } else if (eventName === "turn_completed") {
    const streamingTurn = appState.currentStreamingTurn;
    hydrateFromPayload(data.payload);
    const loggedTurn = data.payload?.ui?.turns?.[data.payload.ui.turns.length - 1] || {};
    const latestTurn = {
      turn_index: data.turn_index,
      timestamp: loggedTurn.timestamp || "",
      user_input: loggedTurn.user_input || streamingTurn?.user_input || {},
      plan: loggedTurn.plan || streamingTurn?.plan || { characters: [] },
      env_feedback: loggedTurn.env_feedback || streamingTurn?.env_feedback || { character_feedback: [], env_summary: "" },
      director_result: loggedTurn.director_result || streamingTurn?.director_result || {},
      stream: streamingTurn?.stream || createEmptyStreamState(),
      is_streaming: false,
      active_stage: null,
    };
    if (Array.isArray(appState.turns) && appState.turns.length) {
      appState.turns = [...appState.turns.slice(0, -1), latestTurn];
    }
  }
}

async function loadInitialState() {
  setConnectionStatus("Loading...", true);
  const gamesPayload = await apiGet("/api/games");
  appState.availableGames = gamesPayload.games || [];
  if (!appState.gameId) {
    const params = new URLSearchParams(window.location.search);
    appState.gameId = params.get("game_id") || gamesPayload.default_game_id || appState.gameId;
  }
  selectedGameId.value = appState.gameId || "";
  const payload = await apiGet("/api/game/state", appState.gameId);
  hydrateFromPayload(payload);
  setConnectionStatus("Connected", true);
}

async function sendMessage(userInput) {
  if (isGameEnded.value) {
    return;
  }
  setConnectionStatus("Streaming...", true);
  isSending.value = true;
  try {
    const response = await fetch(buildApiUrl("/api/game/message/stream"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ user_input: userInput }),
    });
    await consumeSseResponse(response, handleStreamEvent);
    setConnectionStatus("Connected", true);
  } finally {
    isSending.value = false;
  }
}

function buildInputPayload(rawText, choiceId = null, selectedChoice = null) {
  return {
    input_mode: "hybrid",
    raw_text: rawText,
    choice_id: choiceId,
    selected_choice: selectedChoice,
    meta: {},
  };
}

async function onSubmitChat() {
  const rawText = chatInput.value.trim();
  if (!rawText) {
    return;
  }
  try {
    await sendMessage(buildInputPayload(rawText));
    chatInput.value = "";
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

function onChatKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }
  event.preventDefault();
  onSubmitChat();
}

async function onPickChoice(option) {
  if (!option || inputDisabled.value) {
    return;
  }
  try {
    await sendMessage(buildInputPayload(option.text || "", option.id, option.text));
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onResetGame() {
  try {
    setConnectionStatus("Resetting...", true);
    const payload = await apiPost("/api/game/reset", {}, appState.gameId);
    localStorage.removeItem(customizationStorageKey.value);
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onSubmitPlayerCustomization(values) {
  try {
    setConnectionStatus("Saving Player...", true);
    const payload = await apiPost("/api/game/player_customization", { values }, appState.gameId);
    localStorage.setItem(customizationStorageKey.value, "true");
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onSaveGame() {
  const slotId = saveSlotInput.value.trim();
  if (!slotId) {
    window.alert("请输入存档名，例如 slot_1。");
    return;
  }
  try {
    setConnectionStatus("Saving...", true);
    const payload = await apiPost("/api/game/save", { slot_id: slotId }, appState.gameId);
    hydrateFromPayload(payload);
    loadSlotId.value = slotId;
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onLoadGame() {
  if (!loadSlotId.value) {
    window.alert("请先选择要读取的存档。");
    return;
  }
  try {
    setConnectionStatus("Loading Save...", true);
    const payload = await apiPost("/api/game/load", { slot_id: loadSlotId.value }, appState.gameId);
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onSwitchGame() {
  const nextGameId = selectedGameId.value;
  if (!nextGameId || nextGameId === appState.gameId) {
    return;
  }
  appState.gameId = nextGameId;
  syncCustomizationFlag();
  appState.currentStreamingTurn = null;
  appState.selectedCharacterId = null;
  const url = new URL(window.location.href);
  url.searchParams.set("game_id", nextGameId);
  window.history.replaceState({}, "", url);
  try {
    await loadInitialState();
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onActivateGoal(goalId) {
  try {
    setConnectionStatus("Updating Goal...", true);
    const payload = await apiPost("/api/game/goals/activate", { goal_id: goalId }, appState.gameId);
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onDeactivateGoal(goalId) {
  try {
    setConnectionStatus("Updating Goal...", true);
    const payload = await apiPost("/api/game/goals/deactivate", { goal_id: goalId }, appState.gameId);
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

function onSelectCharacter(characterId) {
  appState.selectedCharacterId = characterId;
}

onMounted(async () => {
  try {
    await loadInitialState();
  } catch (error) {
    setConnectionStatus("Offline", false);
    window.alert(error.message);
  }
});
</script>

<template>
  <div class="app-shell">
    <main class="app-frame">
      <aside class="tool-rail">
        <ControlStrip
          v-model:selected-game-id="selectedGameId"
          v-model:save-slot-input="saveSlotInput"
          v-model:load-slot-id="loadSlotId"
          :available-games="appState.availableGames"
          :save-options="appState.saves"
          :connection-text="connectionStatus.text"
          :connection-healthy="connectionStatus.healthy"
          @switch-game="onSwitchGame"
          @reset-game="onResetGame"
          @save-game="onSaveGame"
          @load-game="onLoadGame"
        />
      </aside>

      <section class="workspace-layout">
        <aside class="left-panel">
          <GoalBanner
            :goals-config="appState.state.goals"
            :streaming-turn="appState.currentStreamingTurn"
            @activate-goal="onActivateGoal"
            @deactivate-goal="onDeactivateGoal"
          />

          <StateSidebar
            :state="appState.state"
            :selected-character-id="appState.selectedCharacterId"
            @select-character="onSelectCharacter"
          />
        </aside>

        <section class="right-panel">
          <PlayerCustomizationForm
            v-if="needsPlayerCustomization"
            :fields="customizationFields"
            @submit="onSubmitPlayerCustomization"
          />

          <template v-else>
            <TurnTimeline
              :turns="timelineTurns"
              :opening-text="openingText"
              :stat-rules="appState.state.stat_rules"
              :state="appState.state"
              :interactive="!inputDisabled"
              @pick-option="onPickChoice"
            />

            <form class="input-dock chat-form" @submit.prevent="onSubmitChat">
            <textarea
              id="chat-input"
              v-model="chatInput"
              rows="2"
              :disabled="isGameEnded"
              placeholder="输入你的行动、提问或回应..."
              @keydown="onChatKeydown"
            />
            <div class="chat-actions">
              <button type="submit" :disabled="inputDisabled">
                {{ isGameEnded ? "已达成结局" : needsGoalChoice ? "请先选择目标" : isSending ? "生成中..." : "发送" }}
              </button>
            </div>
            </form>
          </template>
        </section>
      </section>
    </main>
  </div>
</template>
