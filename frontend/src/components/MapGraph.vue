<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  mapLocations: {
    type: Object,
    default: () => ({}),
  },
  characters: {
    type: Object,
    default: () => ({}),
  },
  currentLocation: {
    type: String,
    default: "",
  },
});

const hoveredLocationId = ref("");
const viewBoxWidth = 360;
const viewBoxHeight = 260;
const centerX = viewBoxWidth / 2;
const centerY = viewBoxHeight / 2;
const radius = 92;

const locationIds = computed(() => Object.keys(props.mapLocations || {}));

const characterNamesByLocation = computed(() => {
  const grouped = {};
  Object.values(props.characters || {}).forEach((character) => {
    const state = character?.state || {};
    const location = state.location;
    if (!location) {
      return;
    }
    if (!grouped[location]) {
      grouped[location] = [];
    }
    grouped[location].push(state.name || state.character_id || "unknown");
  });
  return grouped;
});

const nodes = computed(() => {
  const ids = locationIds.value;
  if (!ids.length) {
    return [];
  }
  return ids.map((id, index) => {
    const angle = (-Math.PI / 2) + (index / ids.length) * Math.PI * 2;
    return {
      id,
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
      description: props.mapLocations?.[id]?.description || "",
      connectedLocations: props.mapLocations?.[id]?.connected_locations || [],
      characters: characterNamesByLocation.value[id] || [],
      isCurrent: props.currentLocation === id,
    };
  });
});

const nodeMap = computed(() => {
  return Object.fromEntries(nodes.value.map((node) => [node.id, node]));
});

const edges = computed(() => {
  const seen = new Set();
  const segments = [];
  nodes.value.forEach((node) => {
    node.connectedLocations.forEach((targetId) => {
      const target = nodeMap.value[targetId];
      if (!target) {
        return;
      }
      const edgeId = [node.id, targetId].sort().join("::");
      if (seen.has(edgeId)) {
        return;
      }
      seen.add(edgeId);
      segments.push({
        id: edgeId,
        x1: node.x,
        y1: node.y,
        x2: target.x,
        y2: target.y,
        active: node.isCurrent || target.isCurrent,
      });
    });
  });
  return segments;
});

const hoveredNode = computed(() => {
  if (!hoveredLocationId.value) {
    return null;
  }
  return nodes.value.find((node) => node.id === hoveredLocationId.value) || null;
});
</script>

<template>
  <div class="map-graph-card">
    <svg class="map-graph" :viewBox="`0 0 ${viewBoxWidth} ${viewBoxHeight}`" role="img" aria-label="world map graph">
      <line
        v-for="edge in edges"
        :key="edge.id"
        class="map-edge"
        :class="{ 'map-edge-active': edge.active }"
        :x1="edge.x1"
        :y1="edge.y1"
        :x2="edge.x2"
        :y2="edge.y2"
      />

      <g
        v-for="node in nodes"
        :key="node.id"
        class="map-node"
        :class="{ 'map-node-current': node.isCurrent, 'map-node-hovered': hoveredLocationId === node.id }"
        @mouseenter="hoveredLocationId = node.id"
        @mouseleave="hoveredLocationId = ''"
      >
        <circle class="map-node-dot" :cx="node.x" :cy="node.y" r="18" />
        <text class="map-node-label" :x="node.x" :y="node.y + 34">{{ node.id }}</text>
      </g>
    </svg>

    <div v-if="hoveredNode" class="map-tooltip">
      <div class="map-tooltip-title">{{ hoveredNode.id }}</div>
      <div class="map-tooltip-text">{{ hoveredNode.description || "暂无地点描述。" }}</div>
      <div class="map-tooltip-subtitle">在场角色</div>
      <div v-if="hoveredNode.characters.length" class="tag-list">
        <span v-for="name in hoveredNode.characters" :key="name" class="fact-tag">{{ name }}</span>
      </div>
      <div v-else class="empty-state">当前没有角色停留在这里。</div>
    </div>
    <div v-else class="map-tooltip map-tooltip-placeholder">
      <div class="map-tooltip-title">地图概览</div>
      <div class="map-tooltip-text">悬停地点可查看描述和当前所在角色。</div>
    </div>
  </div>
</template>
