import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable — stay silent */
    }
  };

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-accent/20 bg-black/50">
      <div className="flex items-center justify-between border-b border-accent/10 px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-200/50">
          {language || 'code'}
        </span>
        <button
          type="button"
          onClick={copy}
          title="Copy code"
          className="rounded border border-accent/20 bg-accent/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-cyan-200/60 hover:border-accent/50 hover:text-accent"
        >
          {copied ? '✓ Copied' : '⧉ Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-cyan-100/90">
        <code>{code}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  pre({ children }) {
    return <>{children}</>;
  },
  code({ className, children }) {
    const text = String(children ?? '').replace(/\n$/, '');
    const isBlock = /\n/.test(text) || (className ?? '').includes('language-');
    if (!isBlock) {
      return (
        <code className="rounded bg-accent/10 px-1 py-0.5 font-mono text-[0.85em] text-accent">
          {children}
        </code>
      );
    }
    const match = /language-(\w+)/.exec(className ?? '');
    return <CodeBlock language={match?.[1] ?? ''} code={text} />;
  },
  a({ href, children }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      >
        {children}
      </a>
    );
  },
  ul({ children }) {
    return <ul className="my-1 list-disc space-y-1 pl-5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="my-1 list-decimal space-y-1 pl-5">{children}</ol>;
  },
  p({ children }) {
    return <p className="my-1">{children}</p>;
  },
  h1({ children }) {
    return <p className="my-2 font-mono text-sm font-bold uppercase tracking-[0.15em] text-white">{children}</p>;
  },
  h2({ children }) {
    return <p className="my-2 font-mono text-xs font-bold uppercase tracking-[0.15em] text-cyan-100">{children}</p>;
  },
  h3({ children }) {
    return <p className="my-1 font-mono text-xs font-bold uppercase tracking-[0.15em] text-cyan-200/80">{children}</p>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="my-2 border-l-2 border-gold/50 pl-3 text-cyan-100/70">
        {children}
      </blockquote>
    );
  },
  table({ children }) {
    return (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-xs">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return (
      <th className="border border-accent/20 bg-accent/10 px-2 py-1 text-left font-mono uppercase tracking-wider">
        {children}
      </th>
    );
  },
  td({ children }) {
    return <td className="border border-accent/15 px-2 py-1">{children}</td>;
  },
};

/**
 * Assistant-message markdown. Memoized so streaming deltas in one message
 * don't re-parse the whole transcript; streaming text is passed down fresh.
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="mew-markdown text-sm leading-relaxed text-cyan-100/90">
      <ReactMarkdown components={components}>{text}</ReactMarkdown>
    </div>
  );
});
