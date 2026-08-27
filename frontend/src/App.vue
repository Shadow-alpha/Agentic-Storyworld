<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import ControlStrip from "./components/ControlStrip.vue";
import PlayerCustomizationForm from "./components/PlayerCustomizationForm.vue";
import StateSidebar from "./components/StateSidebar.vue";
import StoryPanel from "./components/StoryPanel.vue";
import TurnTimeline from "./components/TurnTimeline.vue";
import { apiGet, apiPost, authHeaders, getAccessToken, setAccessToken } from "./services/api";
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
    player: {},
    world: {},
    characters: {},
    story: {},
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
const activePanel = ref("records");
const accessChecked = ref(false);
const accessRequired = ref(false);
const accessToken = ref(getAccessToken());
const inviteCode = ref("");
const inviteError = ref("");

const panelTabs = [
  { id: "records", label: "记录" },
  { id: "story", label: "剧情" },
  { id: "characters", label: "角色" },
  { id: "world", label: "世界格局" },
];

const timelineTurns = computed(() => {
  const turns = [...appState.turns];
  if (appState.currentStreamingTurn) {
    turns.push(appState.currentStreamingTurn);
  }
  return turns;
});

const openingText = computed(() => appState.state?.config?.opening || "等待第一轮输入。");
const customizationFields = computed(() => appState.state?.config?.player_customization || {});
const hasCustomizationFields = computed(() => Object.keys(customizationFields.value).length > 0);
const customizationStorageKey = computed(() => `player_customized:${appState.gameId || "unknown"}`);
const needsPlayerCustomization = computed(() => {
  return hasCustomizationFields.value && !timelineTurns.value.length && !customizationCompleted.value;
});
const visiblePanelTabs = computed(() => {
  if (!needsPlayerCustomization.value) {
    return panelTabs;
  }
  return [
    { id: "player_profile", label: "玩家信息" },
    ...panelTabs.filter((tab) => tab.id !== "records"),
  ];
});

const endingState = computed(() => appState.state?.story?.ending_state || {});
const isGameEnded = computed(() => !!endingState.value?.is_ended);
const inputDisabled = computed(() => {
  return isSending.value || isGameEnded.value || needsPlayerCustomization.value;
});
const storyHint = computed(() => {
  const story = appState.state?.story || {};
  if (!story.current && !story.ending_state?.is_ended) {
    return null;
  }
  if (story.ending_state?.is_ended) {
    return {
      label: "结局",
      title: story.ending_state.title || "结局达成",
      meta: "已结束",
    };
  }
  const title = story.title || story.current || "当前事件";
  if (story.status === "unstarted") {
    const parts = [];
    if (story.turns_until_start !== null && story.turns_until_start !== undefined) {
      parts.push(`剩余 ${story.turns_until_start} 回合`);
    }
    const minutes = formatMinutes(story.minutes_until_start);
    if (minutes) {
      parts.push(`约 ${minutes}`);
    }
    return {
      label: "事件未开始",
      title,
      meta: parts.join(" / ") || "等待中",
    };
  }
  const modeLabels = {
    pressure: "事件推进中",
    closure: "事件收束中",
    time_skip: "时间流逝中",
  };
  const parts = [
    story.pace?.soft_turns ? `回合 ${story.turns_since_started || 0}/${story.pace.soft_turns}` : "",
    story.pace?.soft_duration ? `时间 ${story.elapsed_minutes_since_started || 0}/${story.pace.soft_duration} 分钟` : "",
  ].filter(Boolean);
  return {
    label: modeLabels[story.mode] || "事件进行中",
    title,
    meta: parts.join(" / ") || "推进中",
  };
});

function setConnectionStatus(text, healthy = true) {
  connectionStatus.text = text;
  connectionStatus.healthy = healthy;
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

function createEmptyStreamState() {
  return {
    visibleNarrative: "",
    options: [],
    optionsReady: false,
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
    director_plan: { player_input: {}, context: "", characters: [] },
    characters: {},
    director_narrative: {
      narrative: "",
    },
    director_resolve: {
      story_update: {},
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
      player: clonePlain(appState.state.player),
      world: clonePlain(appState.state.world),
    });
  }
  return appState.currentStreamingTurn;
}

function ensureStreamingCharacter(turn, characterId) {
  if (!characterId) {
    return {};
  }
  if (!turn.characters[characterId]) {
    turn.characters[characterId] = {
      id: characterId,
      name: characterId,
      dialogue: { response: "" },
      reflection: {},
      streaming: true,
    };
  }
  return turn.characters[characterId];
}

function mergeCharacterPayload(turn, payload) {
  Object.entries(payload || {}).forEach(([characterId, record]) => {
    const current = ensureStreamingCharacter(turn, characterId);
    turn.characters[characterId] = {
      ...current,
      ...record,
      dialogue: record?.dialogue || current.dialogue || {},
      reflection: record?.reflection || current.reflection || {},
    };
  });
}

function upsertPlannedCharacter(turn, character) {
  if (!character?.id) {
    return;
  }
  const planned = Array.isArray(turn.director_plan?.characters) ? [...turn.director_plan.characters] : [];
  const existingIndex = planned.findIndex((item) => item?.id === character.id);
  const cleanCharacter = {
    id: character.id,
    name: character.name || character.id,
    order: character.order || 1,
  };
  if (existingIndex >= 0) {
    planned[existingIndex] = { ...planned[existingIndex], ...cleanCharacter };
  } else {
    planned.push(cleanCharacter);
  }
  turn.director_plan.characters = planned;
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
      order: parsed.order || 1,
    });
    return;
  }
  if (stage === "director_plan" && eventName === "block_done" && block === "player_input") {
    turn.director_plan.player_input = data.parsed || {};
    return;
  }
  if (stage === "director_plan" && eventName === "block_done" && block === "context") {
    turn.director_plan.context = data.parsed || data.text || "";
    return;
  }

  if (stage === "character") {
    turn.active_stage = "responding";
    const item = ensureStreamingCharacter(turn, data.character_id);
    item.dialogue = item.dialogue || {};
    if (block === "response") {
      item.dialogue.response =
        eventName === "block_done" ? data.parsed || data.text || item.dialogue.response : data.text || item.dialogue.response;
    }
    return;
  }

  if (stage === "director_narrative") {
    if (block === "narrative") {
      turn.active_stage = "narrating";
      const text = eventName === "block_done" ? data.parsed || data.text || "" : data.display_text || data.text || "";
      turn.stream.visibleNarrative = text;
      turn.director_narrative.narrative = text;
    } else if (eventName === "block_done" && block === "time") {
      turn.director_narrative.time = data.parsed || data.text || "";
    } else if (eventName === "block_done" && block === "scene") {
      turn.director_narrative.scene = data.parsed || data.text || "";
    } else if (eventName === "block_done" && block === "summary") {
      turn.director_narrative.summary = data.parsed || data.text || "";
    }
    return;
  }

  if (stage === "director_resolve") {
    turn.active_stage = "resolving";
    if (eventName === "block_done" && block === "story_update") {
      turn.director_resolve.story_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "state_update") {
      turn.director_resolve.state_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "interaction") {
      turn.stream.optionsReady = true;
      turn.active_stage = "choices";
      turn.director_resolve.interaction = data.parsed || { mode: "hybrid", options: [] };
      turn.stream.options = turn.director_resolve.interaction.options || [];
    }
    return;
  }

  if (stage === "character_reflection") {
    const item = ensureStreamingCharacter(turn, data.character_id);
    item.reflection = item.reflection || {};
    if (block === "emotion") {
      item.reflection.emotion = eventName === "block_done" ? data.parsed || data.text || item.reflection.emotion : data.text || item.reflection.emotion;
    } else if (eventName === "block_done" && block === "location" && data.parsed?.value) {
      item.reflection.location = data.parsed;
    } else if (eventName === "block_done" && block === "state_update") {
      item.reflection.state_update = data.parsed || {};
    } else if (eventName === "block_done" && block === "memory_append") {
      item.reflection.memory_append = data.parsed?.text || data.parsed || "";
    }
  }
}

function handleStageDone(turn, data) {
  if (data.stage === "director_plan") {
    turn.director_plan = data.payload?.director_plan || data.plan || turn.director_plan;
  } else if (data.stage === "character") {
    mergeCharacterPayload(turn, data.payload?.characters || {});
    const item = ensureStreamingCharacter(turn, data.character_id);
    item.streaming = false;
  } else if (data.stage === "director_narrative") {
    turn.director_narrative = data.payload?.director_narrative || data.narrative_result || turn.director_narrative;
  } else if (data.stage === "director_resolve") {
    turn.director_resolve = data.payload?.director_resolve || data.resolve_result || turn.director_resolve;
  } else if (data.stage === "character_reflection") {
    mergeCharacterPayload(turn, data.payload?.characters || {});
    const item = ensureStreamingCharacter(turn, data.character_id);
    item.streaming = false;
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
    hydrateFromPayload(data.payload);
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

async function initializeAccess() {
  const status = await apiGet("/api/access/status");
  accessRequired.value = !!status.enabled;
  accessChecked.value = true;
  if (accessRequired.value && !accessToken.value) {
    setConnectionStatus("Invite Required", true);
    return;
  }
  try {
    await loadInitialState();
  } catch (error) {
    if (accessRequired.value && error.status === 401) {
      setAccessToken("");
      accessToken.value = "";
      setConnectionStatus("Invite Required", true);
      return;
    }
    throw error;
  }
}

async function onSubmitInvite() {
  const code = inviteCode.value.trim();
  if (!code) {
    inviteError.value = "请输入邀请码";
    return;
  }
  inviteError.value = "";
  try {
    setConnectionStatus("Checking Invite...", true);
    const payload = await apiPost("/api/access/login", { invite_code: code });
    setAccessToken(payload.access_token || "");
    accessToken.value = getAccessToken();
    await loadInitialState();
  } catch (error) {
    inviteError.value = error.message;
    setConnectionStatus("Invite Failed", false);
  }
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
        ...authHeaders(),
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
  chatInput.value = "";
  try {
    await sendMessage(buildInputPayload(rawText));
  } catch (error) {
    chatInput.value = rawText;
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

async function onLoadGame(slotId = null) {
  const targetSlotId = String(slotId || loadSlotId.value || "").trim();
  if (!targetSlotId) {
    window.alert("请先选择要读取的存档。");
    return;
  }
  try {
    setConnectionStatus("Loading Save...", true);
    const payload = await apiPost("/api/game/load", { slot_id: targetSlotId }, appState.gameId);
    loadSlotId.value = targetSlotId;
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onDeleteSave(slotId) {
  const targetSlotId = String(slotId || loadSlotId.value || "").trim();
  if (!targetSlotId) {
    window.alert("请先选择要删除的存档。");
    return;
  }
  if (!window.confirm(`确定删除存档「${targetSlotId}」吗？`)) {
    return;
  }
  try {
    setConnectionStatus("Deleting Save...", true);
    const payload = await apiPost("/api/game/save/delete", { slot_id: targetSlotId }, appState.gameId);
    if (loadSlotId.value === targetSlotId) {
      loadSlotId.value = "";
    }
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onRenameSave(slotId) {
  const oldSlotId = String(slotId || loadSlotId.value || "").trim();
  if (!oldSlotId) {
    window.alert("请先选择要重命名的存档。");
    return;
  }
  const newSlotId = window.prompt("输入新的存档名：", oldSlotId);
  if (!newSlotId || newSlotId.trim() === oldSlotId) {
    return;
  }
  try {
    setConnectionStatus("Renaming Save...", true);
    const payload = await apiPost(
      "/api/game/save/rename",
      { old_slot_id: oldSlotId, new_slot_id: newSlotId.trim() },
      appState.gameId,
    );
    loadSlotId.value = newSlotId.trim();
    hydrateFromPayload(payload);
    setConnectionStatus("Connected", true);
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

async function onSubmitEditedLatestTurn(rawText) {
  const nextText = String(rawText || "").trim();
  if (!nextText || !appState.turns.length || isSending.value) {
    return;
  }
  try {
    setConnectionStatus("Regenerating...", true);
    const payload = await apiPost("/api/game/turns/revert_latest", {}, appState.gameId);
    hydrateFromPayload(payload);
    await sendMessage(buildInputPayload(nextText));
  } catch (error) {
    setConnectionStatus("Error", false);
    window.alert(error.message);
  }
}

function onRelogin() {
  setAccessToken("");
  accessToken.value = "";
  inviteCode.value = "";
  inviteError.value = "";
  appState.currentStreamingTurn = null;
  setConnectionStatus("Invite Required", true);
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

function onSelectCharacter(characterId) {
  appState.selectedCharacterId = characterId;
}

watch(
  needsPlayerCustomization,
  (needsCustomization) => {
    if (needsCustomization && activePanel.value === "records") {
      activePanel.value = "player_profile";
    } else if (!needsCustomization && activePanel.value === "player_profile") {
      activePanel.value = "records";
    }
  },
  { immediate: true },
);

onMounted(async () => {
  try {
    await initializeAccess();
  } catch (error) {
    if (accessRequired.value) {
      setAccessToken("");
      accessToken.value = "";
    }
    setConnectionStatus("Offline", false);
    window.alert(error.message);
  }
});
</script>

<template>
  <div class="app-shell">
    <main v-if="accessChecked && accessRequired && !accessToken" class="invite-shell">
      <form class="invite-card" @submit.prevent="onSubmitInvite">
        <p class="eyebrow">Private Test</p>
        <h1>输入内测邀请码</h1>
        <p>当前系统处于内测模式，请输入邀请码后继续。</p>
        <input
          v-model="inviteCode"
          type="password"
          autocomplete="off"
          placeholder="Invite code"
        />
        <button type="submit" class="primary-submit">进入内测</button>
        <small v-if="inviteError">{{ inviteError }}</small>
      </form>
    </main>

    <main v-else class="app-frame">
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
          @delete-save="onDeleteSave"
          @rename-save="onRenameSave"
          @relogin="onRelogin"
        />
      </aside>

      <section class="workspace-layout unified-workspace">
        <section class="content-panel">
          <nav class="content-tabs" aria-label="内容切换">
            <button
              v-for="tab in visiblePanelTabs"
              :key="tab.id"
              type="button"
              class="content-tab-button"
              :class="{ active: activePanel === tab.id }"
              @click="activePanel = tab.id"
            >
              {{ tab.label }}
            </button>
          </nav>

          <button
            v-if="storyHint"
            type="button"
            class="story-alert-strip"
            @click="activePanel = 'story'"
          >
            <span class="story-alert-label">{{ storyHint.label }}</span>
            <strong>{{ storyHint.title }}</strong>
            <span v-if="storyHint.meta">{{ storyHint.meta }}</span>
          </button>

          <div class="content-body">
            <PlayerCustomizationForm
              v-if="activePanel === 'player_profile' && needsPlayerCustomization"
              :fields="customizationFields"
              @submit="onSubmitPlayerCustomization"
            />

            <TurnTimeline
              v-else-if="activePanel === 'records'"
              :turns="timelineTurns"
              :opening-text="openingText"
              :stat-rules="appState.state.stat_rules"
              :state="appState.state"
              :game-id="appState.gameId"
              :interactive="!inputDisabled"
              @pick-option="onPickChoice"
              @submit-edit-latest-turn="onSubmitEditedLatestTurn"
            />

            <StoryPanel
              v-else-if="activePanel === 'story'"
              :story="appState.state.story"
            />

            <StateSidebar
              v-else
              :panel="activePanel"
              :game-id="appState.gameId"
              :state="appState.state"
              :selected-character-id="appState.selectedCharacterId"
              @select-character="onSelectCharacter"
            />
          </div>

          <form v-if="!needsPlayerCustomization" class="input-dock chat-form" @submit.prevent="onSubmitChat">
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
                {{ isGameEnded ? "已达成结局" : isSending ? "生成中..." : "发送" }}
              </button>
            </div>
          </form>
        </section>
      </section>
    </main>
  </div>
</template>
