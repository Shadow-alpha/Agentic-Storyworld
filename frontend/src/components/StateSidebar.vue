<script setup>
import { computed, ref } from "vue";
import MapGraph from "./MapGraph.vue";
import { buildWorldStatePreview } from "../utils/format";

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
});

const emit = defineEmits(["select-character"]);
const activeDetailType = ref(null);

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
const userStatePreview = computed(() => {
  const fields = [locationName(props.state?.user_state?.location)];
  for (const [key, value] of statEntries(props.state?.user_state?.stats).slice(0, 2)) {
    fields.push(`${statLabel(key)} ${value?.value ?? value}`);
  }
  return fields.filter(Boolean).join(" · ");
});

function statEntries(stats) {
  return Object.entries(stats || {});
}

function relationEntries(relations) {
  return Object.entries(relations || {});
}

function fieldEntries(value, excludedKeys = ["stats", "relations", "role"]) {
  return Object.entries(value || {})
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .filter(([key]) => !excludedKeys.includes(key));
}

function statLabel(statKey) {
  return statRules.value?.[statKey]?.display_name || statKey;
}

function relationLabel(relationKey) {
  return (
    relationRules.value?.[relationKey]?.display_name ||
    (relationKey === "player" || relationKey === "user" ? "对玩家关系" : relationKey)
  );
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
</script>

<template>
  <aside class="state-panel">
    <section class="state-card clickable-state-card" @click="openDetail('player')">
      <div class="card-header">
        <h3>玩家状态</h3>
        <p>{{ userStatePreview }}</p>
      </div>
      <div class="meta-list">
        <div class="meta-row">
          <span class="meta-label">位置</span>
          <span class="meta-value">{{ locationName(state.user_state?.location) }}</span>
        </div>
      </div>
    </section>

    <section class="state-card clickable-state-card" @click="openDetail('world')">
      <div class="card-header">
        <h3>世界信息</h3>
        <p>{{ buildWorldStatePreview(state.world_state) }}</p>
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
    </section>

    <section class="state-card">
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
          <span class="character-row-name">{{ characterState.state?.name || characterId }}</span>
        </button>
      </div>
    </section>

    <div v-if="activeDetailType || selectedCharacter" class="detail-popover" @click="closeDetail">
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
          <div
            v-for="[fieldKey, fieldValue] in fieldEntries(state.user_state)"
            :key="fieldKey"
            class="meta-row"
          >
            <span class="meta-label">{{ fieldKey === "location" ? "地点" : fieldKey }}</span>
            <span class="meta-value">{{ formatFieldValue(fieldValue, fieldKey) }}</span>
          </div>
          <div v-if="statEntries(state.user_state?.stats).length" class="subcard-block">
            <div class="meta-label">状态</div>
            <div class="delta-list">
              <div v-for="[statKey, statValue] in statEntries(state.user_state?.stats)" :key="statKey" class="delta-row">
                <span class="delta-label">{{ statLabel(statKey) }}</span>
                <span class="delta-change">{{ statValue?.value ?? statValue }}</span>
              </div>
            </div>
          </div>
          <div v-if="relationEntries(state.user_state?.relations).length" class="subcard-block">
            <div class="meta-label">关系</div>
            <div class="delta-list">
              <div
                v-for="[relationKey, relationValue] in relationEntries(state.user_state?.relations)"
                :key="relationKey"
                class="delta-row"
              >
                <span class="delta-label">{{ relationLabel(relationKey) }}</span>
                <span class="delta-change">{{ relationValue?.value ?? relationValue }}</span>
              </div>
            </div>
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
          <div v-if="Object.keys(mapLocations).length" class="subcard-block">
            <div class="meta-label">地图网络</div>
            <MapGraph
              :map-locations="mapLocations"
              :characters="state.characters"
              :current-location="state.user_state?.location || ''"
            />
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
          <div class="meta-label">状态</div>
          <div class="delta-list">
            <div v-for="[statKey, statValue] in statEntries(selectedCharacter.state?.stats)" :key="statKey" class="delta-row">
              <span class="delta-label">{{ statLabel(statKey) }}</span>
              <span class="delta-change">{{ statValue?.value ?? statValue }}</span>
            </div>
          </div>
        </div>
        <div v-if="selectedCharacter && relationEntries(selectedCharacter.state?.relations).length" class="subcard-block">
          <div class="meta-label">关系</div>
          <div class="delta-list">
            <div
              v-for="[relationKey, relationValue] in relationEntries(selectedCharacter.state?.relations)"
              :key="relationKey"
              class="delta-row"
            >
              <span class="delta-label">{{ relationLabel(relationKey) }}</span>
              <span class="delta-change">{{ relationValue?.value ?? relationValue }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
