<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { ChannelConversation, ChatChannel } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const selectedChannel = ref<string>('all')
const searchQuery = ref<string>('')
const selectedConversationId = ref<string | null>(null)
const replyDraft = ref<string>('')

const channelTabs = [
  { key: 'all', label: 'All' },
  { key: 'messenger', label: 'Messenger' },
  { key: 'instagram', label: 'Instagram' },
  { key: 'telegram', label: 'Telegram' },
  { key: 'web', label: 'Web' }
]

async function loadChats(): Promise<void> {
  await erpStore.fetchChats(selectedChannel.value)
  if (erpStore.chats.length > 0 && !erpStore.chats.some((c) => c.id === selectedConversationId.value)) {
    const first = erpStore.chats[0]
    if (first) {
      await selectConversation(first)
    }
  } else if (erpStore.chats.length === 0) {
    selectedConversationId.value = null
    erpStore.activeChatMessages = []
  }
}

onMounted(async () => {
  await loadChats()
})

watch(selectedChannel, async () => {
  await loadChats()
})

watch(
  () => erpStore.selectedCompanyId,
  async () => {
    if (erpStore.isAdmin) {
      selectedConversationId.value = null
      await loadChats()
    }
  }
)

const filteredThreads = computed<ChannelConversation[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return erpStore.chats
  return erpStore.chats.filter(
    (c) =>
      c.customer_name.toLowerCase().includes(q) ||
      c.last_message_preview.toLowerCase().includes(q) ||
      c.external_thread_id.toLowerCase().includes(q)
  )
})

const activeConversation = computed<ChannelConversation | null>(
  () => erpStore.chats.find((c) => c.id === selectedConversationId.value) ?? null
)

async function selectConversation(conv: ChannelConversation): Promise<void> {
  selectedConversationId.value = conv.id
  try {
    await erpStore.fetchChatMessages(conv.id)
  } catch {
    toast.add({
      title: 'Load Failed',
      description: 'Unable to fetch conversation history.',
      color: 'red'
    })
  }
}

async function handleSendReply(): Promise<void> {
  if (!activeConversation.value || !replyDraft.value.trim()) return
  const text = replyDraft.value.trim()
  try {
    await erpStore.replyToChat(activeConversation.value.id, text)
    replyDraft.value = ''
  } catch {
    toast.add({
      title: 'Reply Failed',
      description: 'Could not send message to customer.',
      color: 'red'
    })
  }
}

function channelLabel(channel: ChatChannel): string {
  switch (channel) {
    case 'messenger':
      return 'Messenger'
    case 'instagram':
      return 'Instagram'
    case 'telegram':
      return 'Telegram'
    case 'web':
      return 'Web'
    default:
      return 'Channel'
  }
}

function channelColor(channel: ChatChannel): 'blue' | 'pink' | 'sky' | 'emerald' | 'gray' {
  switch (channel) {
    case 'messenger':
      return 'blue'
    case 'instagram':
      return 'pink'
    case 'telegram':
      return 'sky'
    case 'web':
      return 'emerald'
    default:
      return 'gray'
  }
}

function senderBadgeLabel(senderType: string): string {
  switch (senderType) {
    case 'ai_bot':
      return 'AI Bot'
    case 'manager':
      return 'Manager'
    case 'admin':
      return 'Admin'
    default:
      return 'Customer'
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          Omnichannel Chat Inbox
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Unified Messenger, Instagram, Telegram, and Web customer conversations with AI assistant context.
        </p>
      </div>
      <UButton
        color="gray"
        variant="outline"
        size="sm"
        icon="i-heroicons-arrow-path"
        :loading="erpStore.isLoadingChats"
        @click="loadChats"
      >
        Refresh
      </UButton>
    </div>

    <!-- Channel Filter Tabs -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-1.5">
        <UButton
          v-for="tab in channelTabs"
          :key="tab.key"
          size="xs"
          :color="selectedChannel === tab.key ? 'emerald' : 'gray'"
          :variant="selectedChannel === tab.key ? 'solid' : 'soft'"
          @click="selectedChannel = tab.key"
        >
          {{ tab.label }}
        </UButton>
      </div>

      <div class="w-full sm:w-64">
        <UInput
          v-model="searchQuery"
          placeholder="Search customer or message..."
          icon="i-heroicons-magnifying-glass"
          size="xs"
        />
      </div>
    </div>

    <UAlert
      v-if="erpStore.chatsError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.chatsError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="loadChats">Retry</UButton>
      </template>
    </UAlert>

    <!-- Split Inbox -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-[540px]">
      <!-- Thread List -->
      <UCard
        class="lg:col-span-4 bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 flex flex-col"
        :ui="{ body: { padding: 'p-0' } }"
      >
        <div class="px-4 py-3 border-b border-gray-200 dark:border-slate-800 flex items-center justify-between">
          <span class="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-400">
            Conversations ({{ filteredThreads.length }})
          </span>
        </div>

        <div v-if="erpStore.isLoadingChats" class="p-4 space-y-3">
          <div v-for="n in 4" :key="n" class="h-14 rounded-lg bg-gray-100 dark:bg-slate-800 animate-pulse" />
        </div>

        <div v-else-if="filteredThreads.length === 0" class="p-8 text-center">
          <UIcon name="i-heroicons-chat-bubble-left-ellipsis" class="h-8 w-8 mx-auto text-gray-400 dark:text-slate-600" />
          <p class="mt-2 text-sm font-medium text-gray-900 dark:text-slate-100">No conversations</p>
          <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
            Incoming Messenger, Instagram, and Telegram messages will appear here.
          </p>
        </div>

        <div v-else class="divide-y divide-gray-100 dark:divide-slate-800 overflow-y-auto max-h-[520px]">
          <button
            v-for="conv in filteredThreads"
            :key="conv.id"
            type="button"
            class="w-full text-left px-4 py-3 transition-colors hover:bg-gray-50 dark:hover:bg-slate-800/50"
            :class="selectedConversationId === conv.id ? 'bg-emerald-50/70 dark:bg-emerald-950/40 border-l-2 border-emerald-600' : ''"
            @click="selectConversation(conv)"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-sm font-semibold text-gray-900 dark:text-slate-100 truncate">
                  {{ conv.customer_name }}
                </span>
                <UBadge v-if="conv.unread_count > 0" color="emerald" variant="solid" size="xs">
                  {{ conv.unread_count }}
                </UBadge>
              </div>
              <span class="text-[11px] text-gray-400 dark:text-slate-500 shrink-0 tabular-nums">
                {{ new Date(conv.last_message_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}
              </span>
            </div>
            <div class="flex items-center gap-1.5 mt-1">
              <UBadge :color="channelColor(conv.channel)" variant="soft" size="xs">
                {{ channelLabel(conv.channel) }}
              </UBadge>
              <UBadge v-if="conv.company_name" color="gray" variant="subtle" size="xs">
                {{ conv.company_name }}
              </UBadge>
            </div>
            <p class="mt-1.5 text-xs text-gray-600 dark:text-slate-400 truncate">
              {{ conv.last_message_preview || 'No messages yet' }}
            </p>
          </button>
        </div>
      </UCard>

      <!-- Message Stream & Reply -->
      <UCard
        class="lg:col-span-8 bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 flex flex-col"
        :ui="{ body: { padding: 'p-0 flex-1 flex flex-col' } }"
      >
        <div v-if="!activeConversation" class="flex-1 flex flex-col items-center justify-center p-10 text-center min-h-[420px]">
          <UIcon name="i-heroicons-inbox" class="h-10 w-10 text-gray-300 dark:text-slate-600" />
          <p class="mt-3 text-sm font-medium text-gray-900 dark:text-slate-100">
            Select a conversation to view messages
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
            Review AI bot replies and respond directly to customers.
          </p>
        </div>

        <template v-else>
          <div class="px-5 py-3.5 border-b border-gray-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
                  {{ activeConversation.customer_name }}
                </h2>
                <UBadge :color="channelColor(activeConversation.channel)" variant="soft" size="xs">
                  {{ channelLabel(activeConversation.channel) }}
                </UBadge>
              </div>
              <p class="text-[11px] font-mono text-gray-500 dark:text-slate-400">
                Thread: {{ activeConversation.external_thread_id }}
              </p>
            </div>
            <UBadge color="gray" variant="subtle" size="xs" class="uppercase">
              {{ activeConversation.status }}
            </UBadge>
          </div>

          <div class="flex-1 overflow-y-auto p-5 space-y-3 max-h-[420px] bg-gray-50/50 dark:bg-slate-950/40">
            <div v-if="erpStore.isLoadingChatMessages" class="space-y-3">
              <div v-for="n in 4" :key="n" class="h-12 w-2/3 rounded-lg bg-gray-200 dark:bg-slate-800 animate-pulse" />
            </div>

            <div
              v-else-if="erpStore.activeChatMessages.length === 0"
              class="py-12 text-center text-xs text-gray-500 dark:text-slate-400"
            >
              No messages recorded in this thread yet.
            </div>

            <div
              v-for="msg in erpStore.activeChatMessages"
              v-else
              :key="msg.id"
              class="flex flex-col"
              :class="msg.sender_type === 'customer' ? 'items-start' : 'items-end'"
            >
              <div class="flex items-center gap-1.5 mb-1 text-[11px] text-gray-500 dark:text-slate-400">
                <span class="font-medium">{{ msg.sender_name || senderBadgeLabel(msg.sender_type) }}</span>
                <UBadge
                  :color="msg.sender_type === 'customer' ? 'gray' : msg.sender_type === 'ai_bot' ? 'violet' : 'emerald'"
                  variant="subtle"
                  size="xs"
                >
                  {{ senderBadgeLabel(msg.sender_type) }}
                </UBadge>
                <span class="tabular-nums">
                  {{ new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}
                </span>
              </div>

              <div
                class="max-w-[80%] rounded-xl px-3.5 py-2.5 text-sm shadow-sm whitespace-pre-wrap"
                :class="
                  msg.sender_type === 'customer'
                    ? 'bg-white dark:bg-slate-800 border border-gray-200 dark:border-slate-700 text-gray-900 dark:text-slate-100'
                    : msg.sender_type === 'ai_bot'
                      ? 'bg-violet-50 dark:bg-violet-950/60 border border-violet-200 dark:border-violet-800 text-gray-900 dark:text-slate-100'
                      : 'bg-emerald-600 text-white'
                "
              >
                <p>{{ msg.content }}</p>
                <p
                  v-if="msg.order_code"
                  class="mt-1.5 text-[11px] font-mono opacity-85"
                >
                  Order: {{ msg.order_code }}
                </p>
              </div>
            </div>
          </div>

          <form
            class="p-4 border-t border-gray-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-end gap-2"
            @submit.prevent="handleSendReply"
          >
            <UTextarea
              v-model="replyDraft"
              placeholder="Write a reply to customer..."
              :rows="2"
              autoresize
              class="flex-1"
              @keydown.enter.exact.prevent="handleSendReply"
            />
            <UButton
              type="submit"
              color="emerald"
              variant="solid"
              icon="i-heroicons-paper-airplane"
              :disabled="!replyDraft.trim()"
              :loading="erpStore.isSendingChatReply"
            >
              Send
            </UButton>
          </form>
        </template>
      </UCard>
    </div>
  </div>
</template>
