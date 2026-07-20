<script setup>
import { computed, reactive, watch } from "vue";

const props = defineProps({
  fields: {
    type: Object,
    default: () => ({}),
  },
});

const emit = defineEmits(["submit"]);

const formValues = reactive({});

const fieldEntries = computed(() => Object.entries(props.fields || {}));

function resetValues() {
  for (const key of Object.keys(formValues)) {
    delete formValues[key];
  }
  for (const [key, config] of fieldEntries.value) {
    formValues[key] = config?.default ?? "";
  }
}

function submitForm() {
  emit("submit", { ...formValues });
}

watch(() => props.fields, resetValues, { immediate: true, deep: true });
</script>

<template>
  <form class="player-customization-card" @submit.prevent="submitForm">
    <div class="customization-copy">
      <p class="eyebrow">角色创建</p>
      <h2>先确认你的玩家信息</h2>
      <p>这些内容会作为玩家 profile 进入故事上下文，帮助 Director 和角色理解你的开局身份。</p>
    </div>

    <div class="customization-grid">
      <label v-for="[fieldKey, fieldConfig] in fieldEntries" :key="fieldKey" class="custom-field">
        <span>{{ fieldConfig.label || fieldKey }}</span>
        <select
          v-if="fieldConfig.type === 'choice' && Array.isArray(fieldConfig.options)"
          v-model="formValues[fieldKey]"
        >
          <option v-for="option in fieldConfig.options" :key="option" :value="option">
            {{ option }}
          </option>
        </select>
        <input
          v-else
          v-model="formValues[fieldKey]"
          :type="fieldConfig.type === 'number' ? 'number' : 'text'"
        />
      </label>
    </div>

    <button type="submit" class="customization-submit primary-submit">确认玩家信息</button>
  </form>
</template>
