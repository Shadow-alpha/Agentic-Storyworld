<script setup>
import { computed, reactive, ref, watch } from "vue";

const props = defineProps({
  fields: {
    type: Object,
    default: () => ({}),
  },
});

const emit = defineEmits(["submit"]);

const formValues = reactive({});
const profileText = ref("");

const usesProfileTemplate = computed(() => !!props.fields?.fields || typeof props.fields?.profile?.template === "string");
const fieldConfigs = computed(() => {
  const config = props.fields || {};
  return usesProfileTemplate.value ? config.fields || {} : config;
});
const profileConfig = computed(() => {
  const profile = props.fields?.profile;
  return usesProfileTemplate.value && profile && typeof profile === "object" ? profile : null;
});
const fieldEntries = computed(() => Object.entries(fieldConfigs.value || {}));
const hasProfileTemplate = computed(() => !!profileConfig.value);

function fieldLabel(fieldKey) {
  return fieldConfigs.value?.[fieldKey]?.label || fieldKey;
}

function displayTemplateWithLabels(template) {
  return String(template || "").replace(/\{([^}]+)\}/g, (_, key) => `{${fieldLabel(key.trim())}}`);
}

function resolveProfileTemplate(text) {
  let resolved = String(text || "");
  for (const [fieldKey, fieldConfig] of fieldEntries.value) {
    const label = fieldConfig?.label || fieldKey;
    const value = formValues[fieldKey] ?? "";
    resolved = resolved.split(`{${label}}`).join(value);
    resolved = resolved.split(`{${fieldKey}}`).join(value);
  }
  return resolved.trim();
}

function resetValues() {
  for (const key of Object.keys(formValues)) {
    delete formValues[key];
  }
  for (const [key, config] of fieldEntries.value) {
    formValues[key] = config?.default ?? "";
  }
  profileText.value = hasProfileTemplate.value ? displayTemplateWithLabels(profileConfig.value.template) : "";
}

function submitForm() {
  const values = { ...formValues };
  if (hasProfileTemplate.value) {
    values.profile = resolveProfileTemplate(profileText.value);
  }
  emit("submit", values);
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

    <label v-if="hasProfileTemplate" class="custom-field customization-profile">
      <span>{{ profileConfig.label || "玩家设定" }}</span>
      <textarea
        v-model="profileText"
        rows="8"
      />
    </label>

    <button type="submit" class="customization-submit primary-submit">确认玩家信息</button>
  </form>
</template>
