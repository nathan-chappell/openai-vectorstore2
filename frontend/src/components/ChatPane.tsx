import { ChatKit, type Entity, type UseChatKitOptions, useChatKit } from "@openai/chatkit-react";
import { memo, useCallback, useMemo } from "react";

import { authenticatedFetch, getChatKitConfig } from "../lib/api";
import { MODEL_CHOICES } from "../lib/appConstants";
import type {
  ChatKitClientToolCall,
  ChatKitClientToolResult,
  ChatKitDeeplinkEvent,
  RevealTarget,
} from "../lib/appTypes";
import { stringFromUnknown } from "../lib/uiState";

export const ChatPane = memo(function ChatPane({
  onEntityClick,
  onEntitySearch,
  onClientTool,
  onRevealFile,
}: {
  onEntityClick: (entity: Entity) => void;
  onEntitySearch: (query: string) => Promise<Entity[]>;
  onClientTool: (toolCall: ChatKitClientToolCall) => Promise<ChatKitClientToolResult>;
  onRevealFile: (target: RevealTarget) => Promise<ChatKitClientToolResult>;
}) {
  const chatKitConfig = getChatKitConfig();
  const handleDeeplink = useCallback(
    (event: ChatKitDeeplinkEvent): void => {
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
        uploadStrategy: { type: "direct", uploadUrl: chatKitConfig.attachmentUploadUrl },
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
        greeting: "Ask me to add files, preview splits, search indexed sources, or build a research library.",
        prompts: [
          { label: "Add files", prompt: "Add the files I attach, then tell me when they are indexed and ready to search.", icon: "plus" },
          { label: "Preview split", prompt: "Preview a semantic split for the attached file or pasted text before changing the library.", icon: "settings-slider" },
          { label: "Build research library", prompt: "Build a research library for this topic or paper title, dedupe sources, and keep me posted on indexing.", icon: "book-open" },
          { label: "Search and cite", prompt: "Search the indexed files around this topic, answer from evidence, and cite source titles.", icon: "check-circle" },
        ],
      },
      composer: {
        placeholder: "Attach files, search sources, or build a research library...",
        attachments: {
          enabled: true,
          maxCount: 10,
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
    [chatKitConfig.attachmentUploadUrl, chatKitConfig.domainKey, chatKitConfig.url, handleDeeplink, onClientTool, onEntityClick, onEntitySearch],
  );
  const chatKit = useChatKit(options);
  return <ChatKit control={chatKit.control} className="chatkit-element" />;
});
