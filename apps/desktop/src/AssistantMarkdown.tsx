import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  text: string;
  streaming?: boolean;
};

/** Cursor-like markdown renderer for assistant replies. */
export function AssistantMarkdown({ text, streaming }: Props) {
  if (!text) return null;
  return (
    <div className={`cx-md${streaming ? " is-streaming" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
          code: ({ className, children, ...props }) => {
            const inline = !className;
            if (inline) {
              return (
                <code className="cx-md-code-inline" {...props}>
                  {children}
                </code>
              );
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => <pre className="cx-md-pre">{children}</pre>,
        }}
      >
        {text}
      </ReactMarkdown>
      {streaming ? <span className="assistant-caret" aria-hidden /> : null}
    </div>
  );
}
