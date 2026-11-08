// Shown wherever the knowledge library can show document text. The text is the publisher's: the app only demonstrates with it.
import { Icon } from "./Icon";

export function PrototypeNotice({ sourceUrl }: { sourceUrl?: string | null }) {
  return (
    <p className="prototype-notice">
      <Icon name="book" size={13} />
      <span>
        Documents belong to their publishers and are here only to show how the platform works. To read, quote or rely on one, use the official source
        {sourceUrl ? (
          <>
            : <a href={sourceUrl} target="_blank" rel="noreferrer noopener">open the official source</a>.
          </>
        ) : (
          "."
        )}
      </span>
    </p>
  );
}
