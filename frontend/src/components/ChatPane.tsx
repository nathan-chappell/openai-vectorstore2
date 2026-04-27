import { ChatKit, type Entity, type UseChatKitOptions, useChatKit } from "@openai/chatkit-react";
import { memo, useCallback, useMemo } from "react";

import { authenticatedFetch, getChatKitConfig } from "../lib/api";
import { MODEL_CHOICES } from "../lib/appConstants";
import type { ChatKitClientToolCall, RevealTarget } from "../lib/appTypes";
import { stringFromUnknown } from "../lib/uiState";

export const ChatPane = memo(function ChatPane({
  onEntityClick,
  onEntitySearch,
  onClientTool,
  onRevealFile,
}: {
  onEntityClick: (entity: Entity) => void;
  onEntitySearch: (query: string) => Promise<Entity[]>;
  onClientTool: (toolCall: ChatKitClientToolCall) => Promise<Record<string, unknown>>;
  onRevealFile: (target: RevealTarget) => Promise<Record<string, unknown>>;
}) {
  const chatKitConfig = getChatKitConfig();
  const handleDeeplink = useCallback(
    (event: { name: string; data?: Record<string, unknown> }): void => {
      const sourceIdFromName = event.name.startsWith("source/")
        ? decodeURIComponent(event.name.slice("source/".length))
        : null;
      if (!sourceIdFromName && !["source", "file", "reveal_file", "reveal_source"].includes(event.name)) {
        return;
      }
      const sourceId = stringFromUnknown(event.data?.source_id ?? event.data?.sourceId ?? event.data?.id) ?? sourceIdFromName;
      const entryId = stringFromUnknown(event.data?.entry_id ?? event.data?.entryId);
      void onRevealFile({ sourceId, entryId });
    },
    [onRevealFile],
  );
  const options = useMemo<UseChatKitOptions>(
    () => ({
      api: {
        url: chatKitConfig.url,
        domainKey: chatKitConfig.domainKey,
        fetch: authenticatedFetch,
      },
      theme: {
        colorScheme: "light",
        radius: "round",
        density: "compact",
      },
      history: {
        enabled: true,
        showDelete: false,
        showRename: false,
      },
      header: {
        enabled: true,
        title: { enabled: true, text: "Chat" },
      },
      startScreen: {
        greeting: "Select indexed files, then ask me to search, answer, synthesize, image, or narrate from them.",
        prompts: [
          { label: "Answer from files", prompt: "Answer my question using indexed file matches and cite the source titles.", icon: "check-circle" },
          { label: "Build research library", prompt: "Build a research library for this topic or paper title, dedupe sources, and show progress in the file browser.", icon: "book-open" },
          { label: "Search trails", prompt: "Search the indexed files around this topic and explain the useful trails.", icon: "sparkle" },
          { label: "Generate from evidence", prompt: "Use retrieved indexed file matches as evidence, and separate facts from speculation.", icon: "bolt" },
        ],
      },
      composer: {
        placeholder: "Ask files or build a research library...",
        attachments: {
          enabled: false,
        },
        tools: [
          {
            id: "build_research_library",
            label: "Build research library",
            shortLabel: "Research",
            icon: "book-open",
            pinned: true,
            placeholderOverride: "Topic or paper title to build into a research library",
          },
        ],
        dictation: { enabled: false },
        models: MODEL_CHOICES.map((choice) => ({ ...choice, default: choice.id === "balanced" })),
      },
      threadItemActions: {
        feedback: false,
      },
      entities: {
        onTagSearch: onEntitySearch,
        showComposerMenu: true,
        onClick: onEntityClick,
      },
      onDeeplink: handleDeeplink,
      onClientTool,
    }),
    [chatKitConfig.domainKey, chatKitConfig.url, handleDeeplink, onClientTool, onEntityClick, onEntitySearch],
  );
  const chatKit = useChatKit(options);
  return <ChatKit control={chatKit.control} className="chatkit-element" />;
});
