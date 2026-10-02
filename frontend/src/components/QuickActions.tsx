import { useMemo } from 'react';
import { HudChip } from './hud';
import { useMew } from '../mew/context';
import { runContextOf } from '../mew/steps';

// ---------------------------------------------------------------------------
// Contextual quick actions under the chat input. Every chip does something
// real: prefill chips put text in the input (user completes + sends),
// send chips dispatch a real chat message, and Upload navigates to the
// Knowledge tab. Context is derived from the last completed run's REAL
// tool steps — never invented.
// ---------------------------------------------------------------------------

interface Chip {
  label: string;
  title?: string;
  run: () => void;
}

const DEFAULT_PROMPTS: { label: string; prompt: string; title: string }[] = [
  { label: 'Create Task', prompt: 'Add a task: ', title: 'Prefill a task command' },
  { label: 'Set Reminder', prompt: 'Remind me in 20 minutes to ', title: 'Prefill a reminder command' },
  { label: 'Search', prompt: 'Search the web for ', title: 'Prefill a web search' },
  { label: 'Explain', prompt: 'Explain this code: ', title: 'Prefill a code explanation request' },
  { label: 'Summarize', prompt: 'Summarize my document ', title: 'Prefill a document summary request' },
];

export function QuickActions() {
  const {
    lastRun,
    chatBusy,
    sendChat,
    prefillChat,
    focusChatInput,
    setActiveTab,
  } = useMew();

  const context = useMemo(
    () => (lastRun ? runContextOf(lastRun.steps) : 'none'),
    [lastRun],
  );

  const chips: Chip[] = useMemo(() => {
    if (context === 'docs') {
      return [
        {
          label: 'Summarize my document',
          title: 'Ask Spidey to summarize the document from the last run',
          run: () => sendChat('Summarize my document'),
        },
        {
          label: 'Find key points',
          title: 'Ask Spidey for the key points of the document',
          run: () => sendChat('What are the key points in my document?'),
        },
        {
          label: 'Quiz me',
          title: 'Have Spidey ask you questions about the document',
          run: () => sendChat('Ask me questions about my document'),
        },
      ];
    }
    if (context === 'tasks') {
      return [
        {
          label: 'Show my tasks',
          title: 'List current tasks',
          run: () => sendChat('Show my tasks'),
        },
        {
          label: 'Show my reminders',
          title: 'List current reminders',
          run: () => sendChat('Show my reminders'),
        },
      ];
    }
    return [
      ...DEFAULT_PROMPTS.map((p) => ({
        label: p.label,
        title: p.title,
        run: () => {
          prefillChat(p.prompt);
          focusChatInput();
        },
      })),
      {
        label: 'Upload Document',
        title: 'Open the Knowledge tab to upload a document',
        run: () => setActiveTab('knowledge'),
      },
    ];
  }, [context, sendChat, prefillChat, focusChatInput, setActiveTab]);

  return (
    <div className="mt-3 border-t border-accent/10 pt-3">
      <p className="hud-subtitle mb-2">
        Quick actions
        {context !== 'none' && (
          <span className="ml-2 text-cyan-200/40 normal-case tracking-normal">
            · based on your last run
          </span>
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <HudChip
            key={chip.label}
            title={chip.title}
            onClick={chip.run}
            disabled={chatBusy}
          >
            {chip.label}
          </HudChip>
        ))}
      </div>
    </div>
  );
}
