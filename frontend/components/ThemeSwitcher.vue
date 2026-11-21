<script setup lang="ts">
const colorMode = useColorMode()

const modes: {
  value: 'system' | 'light' | 'dark'
  label: string
  icon: string
}[] = [
  { value: 'system', label: 'System', icon: 'i-heroicons-computer-desktop' },
  { value: 'light', label: 'Light', icon: 'i-heroicons-sun' },
  { value: 'dark', label: 'Dark', icon: 'i-heroicons-moon' }
]

function setMode(preference: 'system' | 'light' | 'dark'): void {
  colorMode.preference = preference
}
</script>

<template>
  <div
    class="inline-flex items-center rounded-lg border border-gray-200 dark:border-slate-700 bg-gray-100 dark:bg-slate-800/80 p-0.5"
    role="group"
    aria-label="Theme preference"
  >
    <button
      v-for="mode in modes"
      :key="mode.value"
      type="button"
      :aria-pressed="colorMode.preference === mode.value"
      :title="`Theme: ${mode.label}`"
      class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors"
      :class="
        colorMode.preference === mode.value
          ? 'bg-white dark:bg-slate-900 text-gray-900 dark:text-slate-100 shadow-sm'
          : 'text-gray-600 dark:text-slate-400 hover:text-gray-900 dark:hover:text-slate-200'
      "
      @click="setMode(mode.value)"
    >
      <UIcon :name="mode.icon" class="h-3.5 w-3.5" />
      <span class="hidden sm:inline">{{ mode.label }}</span>
    </button>
  </div>
</template>
