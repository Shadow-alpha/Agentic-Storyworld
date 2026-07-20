<script setup>
import { ref } from "vue";

defineProps({
  availableGames: {
    type: Array,
    default: () => [],
  },
  saveOptions: {
    type: Array,
    default: () => [],
  },
  selectedGameId: {
    type: String,
    default: "",
  },
  saveSlotInput: {
    type: String,
    default: "",
  },
  loadSlotId: {
    type: String,
    default: "",
  },
  connectionText: {
    type: String,
    default: "Connected",
  },
  connectionHealthy: {
    type: Boolean,
    default: true,
  },
});

const emit = defineEmits([
  "update:selectedGameId",
  "update:saveSlotInput",
  "update:loadSlotId",
  "switch-game",
  "reset-game",
  "save-game",
  "load-game",
  "relogin",
]);

const isExpanded = ref(false);
const activePanel = ref(null);

function togglePanel(panel) {
  activePanel.value = activePanel.value === panel ? null : panel;
}

function closePanel() {
  activePanel.value = null;
}

function emitAndClose(eventName, payload) {
  emit(eventName, payload);
  closePanel();
}

function loadSlot(slotId) {
  emit("update:loadSlotId", slotId);
  emitAndClose("load-game", slotId);
}
</script>

<template>
  <section class="control-strip" :class="{ expanded: isExpanded }" @click.stop>
    <button type="button" class="tool-icon-button menu-button" title="展开工具栏" @click="isExpanded = !isExpanded">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">工具</span>
    </button>

    <button type="button" class="tool-icon-button" title="游戏" @click="togglePanel('game')">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M7 5h10l3 5-8 9-8-9 3-5Z" /><path d="M7 5l5 14 5-14" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">游戏</span>
    </button>

    <button type="button" class="tool-icon-button" title="存档" @click="togglePanel('save')">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M5 20h14V7l-3-3H5v16Z" /><path d="M8 20v-7h8v7M8 4v5h7" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">存档</span>
    </button>

    <button type="button" class="tool-icon-button" title="会话列表" @click="togglePanel('sessions')">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M4 7h7l2 2h7v10H4V7Z" /><path d="M8 13h8M8 16h5" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">会话</span>
    </button>

    <button type="button" class="tool-icon-button" title="重新输入邀请码" @click="emitAndClose('relogin')">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">邀请码</span>
    </button>

    <button type="button" class="tool-icon-button danger-tool" title="从头开始" @click="togglePanel('reset')">
      <span class="tool-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.35-5.65" /><path d="M20 4v6h-6" /></svg>
      </span>
      <span v-if="isExpanded" class="tool-label">重开</span>
    </button>

    <span
      class="connection-dot"
      :class="{ offline: !connectionHealthy }"
      :title="connectionText"
    ></span>

    <div v-if="activePanel" class="tool-popover-backdrop" @click="closePanel"></div>

    <div v-if="activePanel" class="tool-popover" @click.stop>
      <div class="tool-popover-header">
        <strong>
          {{
            activePanel === "game"
              ? "游戏"
              : activePanel === "save"
                ? "存档"
                : activePanel === "sessions"
                  ? "会话列表"
                  : "从头开始"
          }}
        </strong>
      </div>

      <div v-if="activePanel === 'game'" class="tool-panel-body">
        <select :value="selectedGameId" @change="emit('update:selectedGameId', $event.target.value)">
          <option value="">选择游戏</option>
          <option v-for="game in availableGames" :key="game.game_id" :value="game.game_id">
            {{ game.title ? `${game.title} · ${game.game_id}` : game.game_id }}
          </option>
        </select>
        <button type="button" class="secondary-button" @click="emitAndClose('switch-game')">切换游戏</button>
      </div>

      <div v-else-if="activePanel === 'save'" class="tool-panel-body">
        <input
          :value="saveSlotInput"
          type="text"
          placeholder="slot_1"
          @input="emit('update:saveSlotInput', $event.target.value)"
        />
        <button type="button" class="secondary-button" @click="emitAndClose('save-game')">保存当前会话</button>
      </div>

      <div v-else-if="activePanel === 'sessions'" class="tool-panel-body">
        <p v-if="!saveOptions.length" class="tool-popover-text">暂无存档会话。</p>
        <template v-else>
          <button
            v-for="slot in saveOptions"
            :key="slot.slot_id || slot"
            type="button"
            class="session-item"
            @click="loadSlot(slot.slot_id || slot)"
          >
            <span>{{ slot.slot_id || slot }}</span>
            <small v-if="slot.saved_at">{{ slot.saved_at }}</small>
          </button>
        </template>
      </div>

      <div v-else-if="activePanel === 'reset'" class="tool-panel-body">
        <p class="tool-popover-text">确定要从头开始吗？当前进度会被重置，请确认已经存档。</p>
        <button type="button" class="secondary-button danger-confirm" @click="emitAndClose('reset-game')">
          确认从头开始
        </button>
        <button type="button" class="secondary-button ghost-button" @click="closePanel">取消</button>
      </div>
    </div>
  </section>
</template>
