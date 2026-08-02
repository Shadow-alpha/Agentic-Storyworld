<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  state: {
    type: Object,
    default: () => ({
      user_state: {},
      world_state: {},
      characters: {},
    }),
  },
  selectedCharacterId: {
    type: String,
    default: null,
  },
  panel: {
    type: String,
    default: "all",
  },
  gameId: {
    type: String,
    default: "",
  },
});

const emit = defineEmits(["select-character"]);
const activeDetailType = ref(null);
const failedCharacterImages = ref({});

watch(
  () => props.gameId,
  () => {
    failedCharacterImages.value = {};
  }
);

const characterEntries = computed(() => Object.entries(props.state?.characters || {}));
const selectedCharacter = computed(() => {
  if (!props.selectedCharacterId) {
    return null;
  }
  return props.state?.characters?.[props.selectedCharacterId] || null;
});
const selectedCharacterId = computed(() => props.selectedCharacterId || "");
const mapLocations = computed(() => props.state?.world_state?.map_locations || {});
const statRules = computed(() => props.state?.stat_rules || props.state?.config?.stat_rules || {});
const relationRules = computed(() => props.state?.relation_rules || props.state?.config?.relation_rules || {});
const currentLocationInfo = computed(() => mapLocations.value?.[props.state?.user_state?.location] || null);
const organizationEntries = computed(() => Object.entries(props.state?.world_state?.organizations || {}));
const showPlayer = computed(() => props.panel === "all" || props.panel === "characters");
const showCharacters = computed(() => props.panel === "all" || props.panel === "characters");
const showWorld = computed(() => props.panel === "all" || props.panel === "world");
const playerProfile = computed(() => props.state?.player_profile || props.state?.user_state?.player_profile || "");

function statEntries(stats) {
  return Object.entries(stats || {});
}

function relationEntries(relations) {
  const rows = [];
  Object.entries(relations || {}).forEach(([targetKey, targetValue]) => {
    if (targetValue && typeof targetValue === "object" && !Array.isArray(targetValue) && !("value" in targetValue)) {
      Object.entries(targetValue).forEach(([relationKey, relationValue]) => {
        rows.push({
          key: `${targetKey}.${relationKey}`,
          targetKey,
          relationKey,
          value: relationValue,
        });
      });
      return;
    }
    rows.push({
      key: `player.${targetKey}`,
      targetKey: "player",
      relationKey: targetKey,
      value: targetValue,
    });
  });
  return rows;
}

function fieldEntries(value, excludedKeys = ["stats", "relations", "role", "player_profile"]) {
  return Object.entries(value || {})
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .filter(([key]) => !excludedKeys.includes(key));
}

function statLabel(statKey) {
  return statRules.value?.[statKey]?.display_name || statKey;
}

function statRule(statKey) {
  return statRules.value?.[statKey] || {};
}

function relationLabel(relationKey) {
  return (
    relationRules.value?.[relationKey]?.display_name ||
    (relationKey === "player" || relationKey === "user" ? "对玩家关系" : relationKey)
  );
}

function relationRule(relationKey) {
  return relationRules.value?.[relationKey] || {};
}

function valueOf(item) {
  return item && typeof item === "object" && "value" in item ? item.value : item;
}

function rangeFor(rule) {
  const range = rule?.range;
  return Array.isArray(range) && range.length === 2 ? range : [0, 100];
}

function percentFor(value, rule) {
  const [min, max] = rangeFor(rule).map(Number);
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || !Number.isFinite(min) || !Number.isFinite(max) || max === min) {
    return 0;
  }
  return Math.max(0, Math.min(100, ((numeric - min) / (max - min)) * 100));
}

function rangeText(rule) {
  const [min, max] = rangeFor(rule);
  return `${min} - ${max}`;
}

function organizationIcon(type) {
  const normalized = String(type || "").toLowerCase();
  const icons = {
    academy: "学",
    company: "商",
    family: "族",
    guild: "会",
    sect: "宗",
  };
  return icons[normalized] || "势";
}

function locationName(locationId) {
  if (!locationId) {
    return "未知";
  }
  return mapLocations.value?.[locationId]?.name || locationId;
}

function formatFieldValue(value, key = "") {
  if (key === "location") {
    return locationName(value);
  }
  if (Array.isArray(value)) {
    return value.join("、");
  }
  if (value && typeof value === "object") {
    if ("value" in value) {
      return value.value;
    }
    return JSON.stringify(value, null, 2);
  }
  return value;
}

function openDetail(type) {
  activeDetailType.value = type;
  emit("select-character", null);
}

function openCharacter(characterId) {
  activeDetailType.value = null;
  emit("select-character", characterId);
}

function closeDetail() {
  activeDetailType.value = null;
  emit("select-character", null);
}

function characterImageUrl(characterId) {
  if (!props.gameId || !characterId) {
    return "";
  }
  const filename = `character_${characterId}.png`;
  return `/api/game/image/${encodeURIComponent(filename)}?game_id=${encodeURIComponent(props.gameId)}`;
}

function hasCharacterImage(characterId) {
  return !!characterImageUrl(characterId) && !failedCharacterImages.value[characterId];
}

function markCharacterImageFailed(characterId) {
  failedCharacterImages.value = {
    ...failedCharacterImages.value,
    [characterId]: true,
  };
}
</script>

<template>
  <aside class="state-panel">
    <section v-if="showPlayer" class="state-card clickable-state-card" @click="openDetail('player')">
      <div class="card-header">
        <h3>玩家状态</h3>
      </div>
      <div class="meta-list">
        <div class="meta-row">
          <span class="meta-label">位置</span>
          <span class="meta-value">{{ locationName(state.user_state?.location) }}</span>
        </div>
      </div>
      <div v-if="statEntries(state.user_state?.stats).length" class="state-bar-list compact-state-bars">
        <div v-for="[statKey, statValue] in statEntries(state.user_state?.stats)" :key="statKey" class="state-bar-row">
          <div class="state-bar-meta">
            <span>{{ statLabel(statKey) }}</span>
            <span>{{ valueOf(statValue) }} / {{ rangeText(statRule(statKey)) }}</span>
          </div>
          <div class="state-bar-track">
            <span class="state-bar-fill" :style="{ width: `${percentFor(valueOf(statValue), statRule(statKey))}%` }"></span>
          </div>
        </div>
      </div>
    </section>

    <section v-if="showWorld" class="state-card clickable-state-card" @click="openDetail('world')">
      <div class="card-header">
        <h3>世界信息</h3>
      </div>
      <div class="meta-list">
        <div class="meta-row">
          <span class="meta-label">时间</span>
          <span class="meta-value">{{ state.world_state?.time || "未知" }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">天气</span>
          <span class="meta-value">{{ state.world_state?.weather || "未知" }}</span>
        </div>
      </div>
      <div v-if="statEntries(state.world_state?.stats).length" class="state-bar-list compact-state-bars">
        <div v-for="[statKey, statValue] in statEntries(state.world_state?.stats)" :key="statKey" class="state-bar-row">
          <div class="state-bar-meta">
            <span>{{ statLabel(statKey) }}</span>
            <span>{{ valueOf(statValue) }} / {{ rangeText(statRule(statKey)) }}</span>
          </div>
          <div class="state-bar-track">
            <span class="state-bar-fill" :style="{ width: `${percentFor(valueOf(statValue), statRule(statKey))}%` }"></span>
          </div>
        </div>
      </div>
    </section>

    <section v-if="showCharacters" class="state-card">
      <div class="card-header">
        <h3>角色</h3>
        <p>{{ characterEntries.length }} characters</p>
      </div>
      <div class="character-list">
        <button
          v-for="[characterId, characterState] in characterEntries"
          :key="characterId"
          type="button"
          class="character-row"
          :class="{ active: selectedCharacterId === characterId }"
          @click="openCharacter(characterId)"
        >
          <img
            v-if="hasCharacterImage(characterId)"
            class="character-avatar-image"
            :src="characterImageUrl(characterId)"
            :alt="characterState.state?.name || characterId"
            loading="lazy"
            @error="markCharacterImageFailed(characterId)"
          />
          <span v-else class="character-avatar-placeholder">
            {{ (characterState.state?.name || characterId).slice(0, 1) }}
          </span>
          <span class="character-row-name">{{ characterState.state?.name || characterId }}</span>
        </button>
      </div>
    </section>

    <section v-if="panel === 'world'" class="state-card world-structure-card">
      <div class="card-header">
        <h3>地点</h3>
      </div>
      <div class="world-list">
        <article v-for="[locationId, location] in Object.entries(mapLocations)" :key="locationId" class="world-list-item">
          <strong>{{ location.name || locationId }}</strong>
          <p>{{ location.description || "" }}</p>
          <div v-if="location.connections" class="detail-text">连接：{{ location.connections }}</div>
          <div v-if="location.notes" class="detail-text">提示：{{ location.notes }}</div>
        </article>
      </div>
    </section>

    <section v-if="panel === 'world' && organizationEntries.length" class="state-card world-structure-card">
      <div class="card-header">
        <h3>组织</h3>
      </div>
      <div class="world-list">
        <article v-for="[organizationId, organization] in organizationEntries" :key="organizationId" class="world-list-item">
          <strong>
            {{ organization.name || organizationId }}
            <span class="organization-type-icon" :title="organization.type || 'organization'">
              {{ organizationIcon(organization.type) }}
            </span>
          </strong>
          <p>{{ organization.description || "" }}</p>
        </article>
      </div>
    </section>

    <div v-if="activeDetailType || (showCharacters && selectedCharacter)" class="detail-popover" @click="closeDetail">
      <div class="popover-panel" @click.stop>
        <div class="card-header">
          <div>
            <p class="eyebrow compact-eyebrow">
              {{ activeDetailType === "player" ? "Player Detail" : activeDetailType === "world" ? "World Detail" : "Character Detail" }}
            </p>
            <h3 v-if="activeDetailType === 'player'">{{ state.player_display_name || "玩家" }}</h3>
            <h3 v-else-if="activeDetailType === 'world'">世界信息</h3>
            <h3 v-else>{{ selectedCharacter.state?.name || selectedCharacterId }}</h3>
          </div>
        </div>

        <div v-if="activeDetailType === 'player'" class="meta-list">
          <div v-if="playerProfile" class="subcard-block profile-block">
            <div class="meta-label">玩家 Profile</div>
            <div class="detail-text profile-text">{{ playerProfile }}</div>
          </div>
          <div v-else class="detail-text profile-text">
            暂无玩家 Profile。
          </div>
        </div>

        <div v-else-if="activeDetailType === 'world'" class="meta-list">
          <div
            v-for="[fieldKey, fieldValue] in fieldEntries(state.world_state, ['map_locations', 'role'])"
            :key="fieldKey"
            class="meta-row"
          >
            <span class="meta-label">{{ fieldKey }}</span>
            <span class="meta-value">{{ formatFieldValue(fieldValue, fieldKey) }}</span>
          </div>
          <div v-if="currentLocationInfo" class="subcard-block">
            <div class="meta-label">当前位置</div>
            <div class="detail-text">{{ currentLocationInfo.description || "" }}</div>
            <div v-if="currentLocationInfo.connected_locations?.length" class="tag-list location-links">
              <span v-for="location in currentLocationInfo.connected_locations" :key="location" class="fact-tag">
                {{ locationName(location) }}
              </span>
            </div>
          </div>
        </div>

        <div v-else class="meta-list">
          <div
            v-for="[fieldKey, fieldValue] in fieldEntries(selectedCharacter.state)"
            :key="fieldKey"
            class="meta-row"
          >
            <span class="meta-label">{{ fieldKey === "location" ? "地点" : fieldKey }}</span>
            <span class="meta-value">{{ formatFieldValue(fieldValue, fieldKey) }}</span>
          </div>
        </div>

        <div v-if="selectedCharacter && statEntries(selectedCharacter.state?.stats).length" class="subcard-block">
          <div class="state-bar-list">
            <div v-for="[statKey, statValue] in statEntries(selectedCharacter.state?.stats)" :key="statKey" class="state-bar-row">
              <div class="state-bar-meta">
                <span>{{ statLabel(statKey) }}</span>
                <span>{{ valueOf(statValue) }} / {{ rangeText(statRule(statKey)) }}</span>
              </div>
              <div class="state-bar-track">
                <span class="state-bar-fill" :style="{ width: `${percentFor(valueOf(statValue), statRule(statKey))}%` }"></span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="selectedCharacter && relationEntries(selectedCharacter.state?.relations).length" class="subcard-block">
          <div class="state-bar-list">
            <div
              v-for="relation in relationEntries(selectedCharacter.state?.relations)"
              :key="relation.key"
              class="state-bar-row"
            >
              <div class="state-bar-meta">
                <span>{{ relationLabel(relation.relationKey) }}</span>
                <span>{{ valueOf(relation.value) }} / {{ rangeText(relationRule(relation.relationKey)) }}</span>
              </div>
              <div class="state-bar-track">
                <span
                  class="state-bar-fill relation-fill"
                  :style="{ width: `${percentFor(valueOf(relation.value), relationRule(relation.relationKey))}%` }"
                ></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
